from __future__ import annotations

import math
import statistics

from traderia.config import AgentConfig
from traderia.models import Action, AttributionRow, BenchmarkReturn, DecisionExplanation, EffectivenessReport
from traderia.storage import SQLiteStore


def effectiveness_report(store: SQLiteStore, config: AgentConfig) -> EffectivenessReport:
    snapshots = store.rows("SELECT total_value FROM portfolio_snapshots ORDER BY timestamp")
    orders = store.rows("SELECT symbol, action, status, price, quantity, fees FROM orders ORDER BY timestamp")

    if not snapshots:
        return EffectivenessReport(config.starting_cash, config.starting_cash, 0.0, 0.0, 0.0, 0.0, 0.0, 0)

    values = [float(row["total_value"]) for row in snapshots]
    ending = values[-1]
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, (value / peak) - 1)

    lots: dict[str, list[tuple[int, float]]] = {}
    trade_pnls: list[float] = []
    for row in orders:
        if row["status"] != "FILLED":
            continue
        action = row["action"]
        quantity = int(row["quantity"])
        price = float(row["price"])
        fees = float(row["fees"])
        symbol = row["symbol"]
        if action == "BUY":
            lots.setdefault(symbol, []).append((quantity, price))
            continue
        if action == "SELL":
            remaining = quantity
            cost = 0.0
            while remaining > 0 and lots.get(symbol):
                lot_qty, lot_price = lots[symbol].pop(0)
                matched = min(remaining, lot_qty)
                cost += matched * lot_price
                remaining -= matched
                if lot_qty > matched:
                    lots[symbol].insert(0, (lot_qty - matched, lot_price))
            trade_pnls.append((quantity * price) - cost - fees)

    winners = [pnl for pnl in trade_pnls if pnl > 0]
    losers = [pnl for pnl in trade_pnls if pnl < 0]
    profit_factor = sum(winners) / abs(sum(losers)) if losers else float("inf") if winners else 0.0
    win_rate = (len(winners) / len(trade_pnls) * 100) if trade_pnls else 0.0
    total_return = ending - config.starting_cash
    returns = _returns(values)
    benchmarks = benchmark_returns(store)

    drawdown_abs = abs(max_drawdown)
    annualised_return = (ending / config.starting_cash) ** (252 / max(1, len(values))) - 1
    calmar = (annualised_return / drawdown_abs) if drawdown_abs > 0 else 0.0

    return EffectivenessReport(
        starting_cash=config.starting_cash,
        ending_value=ending,
        total_return=total_return,
        total_return_pct=(total_return / config.starting_cash) * 100,
        max_drawdown_pct=max_drawdown * 100,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        trades=len(trade_pnls),
        sharpe_ratio=_sharpe_ratio(returns),
        sortino_ratio=_sortino_ratio(returns),
        calmar_ratio=calmar,
        benchmarks=benchmarks,
    )


def attribution_report(store: SQLiteStore) -> list[AttributionRow]:
    """Per-symbol, per-exit-type breakdown of P&L and signal strengths."""
    rows = store.rows(
        """
        SELECT
            o.symbol,
            o.price AS exit_price,
            o.quantity,
            o.fees,
            o.reason AS exit_reason,
            b.price AS entry_price,
            c.timing_score,
            c.sentiment_score,
            c.market_regime_score,
            c.momentum
        FROM orders o
        JOIN orders b ON b.symbol = o.symbol AND b.action = 'BUY' AND b.status = 'FILLED'
        LEFT JOIN market_contexts c ON c.symbol = o.symbol AND c.timestamp = o.timestamp
        WHERE o.action = 'SELL' AND o.status = 'FILLED'
        ORDER BY o.symbol, o.timestamp
        """
    )

    buckets: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        symbol = str(row["symbol"])
        reason = str(row["exit_reason"] or "")
        exit_type = _classify_exit(reason)
        key = (symbol, exit_type)
        entry_px = float(row["entry_price"] or 0)
        exit_px = float(row["exit_price"])
        qty = int(row["quantity"])
        fees = float(row["fees"])
        pnl = (exit_px - entry_px) * qty - fees if entry_px else 0.0
        buckets.setdefault(key, []).append({
            "pnl": pnl,
            "timing": float(row["timing_score"] or 0),
            "sentiment": float(row["sentiment_score"] or 0),
            "regime": float(row["market_regime_score"] or 0),
            "momentum": float(row["momentum"] or 0),
        })

    result: list[AttributionRow] = []
    for (symbol, exit_type), trades in sorted(buckets.items()):
        result.append(AttributionRow(
            symbol=symbol,
            exit_type=exit_type,
            trade_count=len(trades),
            avg_pnl=statistics.fmean(t["pnl"] for t in trades),
            avg_timing=statistics.fmean(t["timing"] for t in trades),
            avg_sentiment=statistics.fmean(t["sentiment"] for t in trades),
            avg_regime=statistics.fmean(t["regime"] for t in trades),
            avg_momentum=statistics.fmean(t["momentum"] for t in trades),
        ))
    return result


def benchmark_returns(store: SQLiteStore) -> tuple[BenchmarkReturn, ...]:
    rows = store.rows(
        """
        SELECT
            symbol,
            FIRST_VALUE(price) OVER (
                PARTITION BY symbol ORDER BY timestamp
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS starting_price,
            FIRST_VALUE(price) OVER (
                PARTITION BY symbol ORDER BY timestamp DESC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS ending_price,
            total_value
        FROM benchmark_snapshots
        ORDER BY symbol, timestamp
        """
    )
    by_symbol: dict[str, list[float]] = {}
    prices: dict[str, tuple[float, float]] = {}
    for row in rows:
        symbol = str(row["symbol"])
        prices[symbol] = (float(row["starting_price"]), float(row["ending_price"]))
        by_symbol.setdefault(symbol, []).append(float(row["total_value"]))

    benchmarks: list[BenchmarkReturn] = []
    for symbol in sorted(by_symbol):
        starting, ending = prices[symbol]
        values = by_symbol[symbol]
        benchmarks.append(
            BenchmarkReturn(
                symbol=symbol,
                starting_price=starting,
                ending_price=ending,
                total_return_pct=((ending / starting) - 1) * 100 if starting else 0.0,
                max_drawdown_pct=_max_drawdown_pct(values),
                sharpe_ratio=_sharpe_ratio(_returns(values)),
            )
        )
    return tuple(benchmarks)


def decision_explanations(store: SQLiteStore, limit: int = 20, action: str | None = None) -> list[DecisionExplanation]:
    params: list[object] = []
    action_filter = ""
    if action:
        action_filter = "AND d.action = ?"
        params.append(action.upper())
    params.append(limit)

    rows = store.rows(
        f"""
        SELECT
            d.symbol,
            d.timestamp,
            d.action,
            d.confidence,
            d.quantity,
            d.price,
            d.expected_edge,
            d.reason,
            c.timing_score,
            c.market_regime_score,
            c.sentiment_score,
            c.momentum,
            c.volatility,
            c.volume_ratio,
            c.short_ma,
            c.long_ma,
            COALESCE(c.rsi, 50.0) AS rsi,
            COALESCE(c.macd_histogram, 0.0) AS macd_histogram,
            COALESCE(c.atr, 0.0) AS atr,
            COALESCE(c.bb_pct, 0.5) AS bb_pct,
            COALESCE(o.status, 'NO_ORDER') AS order_status,
            COALESCE(o.reason, '') AS order_reason
        FROM decisions d
        LEFT JOIN market_contexts c
            ON c.symbol = d.symbol
            AND c.timestamp = d.timestamp
        LEFT JOIN orders o
            ON o.symbol = d.symbol
            AND o.timestamp = d.timestamp
            AND o.action = d.action
            AND o.price = d.price
        WHERE 1 = 1
        {action_filter}
        ORDER BY d.timestamp DESC, d.id DESC
        LIMIT ?
        """,
        tuple(params),
    )

    explanations: list[DecisionExplanation] = []
    for row in rows:
        explanations.append(
            DecisionExplanation(
                symbol=str(row["symbol"]),
                timestamp=_parse_timestamp(str(row["timestamp"])),
                action=Action(str(row["action"])),
                confidence=float(row["confidence"]),
                quantity=int(row["quantity"]),
                price=float(row["price"]),
                expected_edge=float(row["expected_edge"]),
                reason=str(row["reason"]),
                timing_score=_float_or(row["timing_score"], 0.0),
                market_regime_score=_float_or(row["market_regime_score"], 0.0),
                sentiment_score=_float_or(row["sentiment_score"], 0.0),
                momentum=_float_or(row["momentum"], 0.0),
                volatility=_float_or(row["volatility"], 0.0),
                volume_ratio=_float_or(row["volume_ratio"], 1.0),
                short_ma=_float_or(row["short_ma"], 0.0),
                long_ma=_float_or(row["long_ma"], 0.0),
                rsi=_float_or(row["rsi"], 50.0),
                macd_histogram=_float_or(row["macd_histogram"], 0.0),
                atr=_float_or(row["atr"], 0.0),
                bb_pct=_float_or(row["bb_pct"], 0.5),
                order_status=str(row["order_status"]),
                order_reason=str(row["order_reason"]),
            )
        )
    return explanations


def _classify_exit(reason: str) -> str:
    for keyword in ("stop_loss", "trailing_stop", "take_profit", "momentum_breakdown", "negative_reversal", "growth_overlay_switch"):
        if keyword in reason:
            return keyword
    return "signal"


def _returns(values: list[float]) -> list[float]:
    return [(values[index] / values[index - 1]) - 1 for index in range(1, len(values)) if values[index - 1]]


def _max_drawdown_pct(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, (value / peak) - 1)
    return max_drawdown * 100


def _sharpe_ratio(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    volatility = statistics.pstdev(returns)
    if volatility == 0:
        return 0.0
    return statistics.fmean(returns) / volatility * math.sqrt(252)


def _sortino_ratio(returns: list[float]) -> float:
    if not returns:
        return 0.0
    downside = [value for value in returns if value < 0]
    if len(downside) < 2:
        return 0.0
    downside_deviation = statistics.pstdev(downside)
    if downside_deviation == 0:
        return 0.0
    return statistics.fmean(returns) / downside_deviation * math.sqrt(252)


def _float_or(value: object, default: float) -> float:
    return default if value is None else float(value)


def _parse_timestamp(value: str):
    from datetime import datetime
    return datetime.fromisoformat(value)
