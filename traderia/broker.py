from __future__ import annotations

import statistics
from dataclasses import replace
from datetime import datetime

from traderia.config import AgentConfig
from traderia.models import Action, Decision, Order, PortfolioSnapshot, Position


class PaperBroker:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.cash = config.starting_cash
        self.positions: dict[str, Position] = {}
        self.realized_pnl: list[float] = []

    def execute(self, decision: Decision) -> Order:
        if decision.action is Action.HOLD or decision.quantity <= 0:
            return Order(
                symbol=decision.symbol,
                timestamp=decision.timestamp,
                action=Action.HOLD,
                quantity=0,
                price=decision.price,
                fees=0.0,
                status="SKIPPED",
                reason=decision.reason,
            )

        if decision.action is Action.BUY:
            exec_price = decision.price * (1.0 + self.config.slippage_pct + self.config.spread_pct / 2.0)
            fees = exec_price * decision.quantity * self.config.fee_pct
            gross = exec_price * decision.quantity
            total = gross + fees
            if total > self.cash and not self._allows_overlay_margin(decision, gross):
                return self._rejected(decision, "insufficient cash")

            current = self.positions.get(decision.symbol, Position(decision.symbol, 0, 0.0))
            new_qty = current.quantity + decision.quantity
            avg_price = ((current.avg_price * current.quantity) + gross) / new_qty
            self.positions[decision.symbol] = Position(decision.symbol, new_qty, avg_price)
            self.cash -= total
            return self._filled(decision, exec_price, fees)

        # SELL — apply slippage & spread as cost (worse fill)
        exec_price = decision.price * (1.0 - self.config.slippage_pct - self.config.spread_pct / 2.0)
        fees = exec_price * decision.quantity * self.config.fee_pct
        gross = exec_price * decision.quantity

        current = self.positions.get(decision.symbol)
        if current is None or current.quantity < decision.quantity:
            return self._rejected(decision, "insufficient shares")

        pnl = (exec_price - current.avg_price) * decision.quantity - fees
        self.realized_pnl.append(pnl)
        remaining = current.quantity - decision.quantity
        if remaining == 0:
            del self.positions[decision.symbol]
        else:
            self.positions[decision.symbol] = replace(current, quantity=remaining)
        self.cash += gross - fees
        return self._filled(decision, exec_price, fees)

    def snapshot(self, timestamp: datetime, prices: dict[str, float]) -> PortfolioSnapshot:
        equity = 0.0
        for symbol, position in self.positions.items():
            equity += position.quantity * prices.get(symbol, position.avg_price)
        return PortfolioSnapshot(timestamp=timestamp, cash=self.cash, equity_value=equity, total_value=self.cash + equity)

    def position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def portfolio_concentration_score(self, prices: dict[str, float]) -> float:
        """Herfindahl index of position weights. 1.0 = fully concentrated, 1/n = evenly spread."""
        total = self.cash
        values: list[float] = []
        for symbol, pos in self.positions.items():
            v = pos.quantity * prices.get(symbol, pos.avg_price)
            total += v
            values.append(v)
        if total <= 0 or not values:
            return 0.0
        weights = [v / total for v in values]
        return sum(w * w for w in weights)

    def correlation_penalty(self, histories: dict[str, list[float]]) -> float:
        """Returns avg pairwise correlation among held symbols. 0 = uncorrelated, 1 = perfect correlation."""
        held = [s for s in self.positions if s in histories]
        if len(held) < 2:
            return 0.0
        pairs: list[float] = []
        for i in range(len(held)):
            for j in range(i + 1, len(held)):
                a, b = histories[held[i]], histories[held[j]]
                n = min(len(a), len(b))
                if n < 5:
                    continue
                a, b = a[-n:], b[-n:]
                try:
                    pairs.append(abs(statistics.correlation(a, b)))
                except statistics.StatisticsError:
                    pass
        return statistics.fmean(pairs) if pairs else 0.0

    def _allows_overlay_margin(self, decision: Decision, gross: float) -> bool:
        if self.config.mode not in {"overlay", "growth-overlay"}:
            return False
        current = self.positions.get(decision.symbol, Position(decision.symbol, 0, 0.0))
        current_notional = current.quantity * decision.price
        portfolio_value = self.cash + current_notional
        if portfolio_value <= 0:
            return False
        resulting_notional = current_notional + gross
        return resulting_notional <= portfolio_value * self.config.overlay_max_exposure

    def _filled(self, decision: Decision, exec_price: float, fees: float) -> Order:
        return Order(
            symbol=decision.symbol,
            timestamp=decision.timestamp,
            action=decision.action,
            quantity=decision.quantity,
            price=exec_price,
            fees=fees,
            status="FILLED",
            reason=decision.reason,
        )

    def _rejected(self, decision: Decision, reason: str) -> Order:
        return Order(
            symbol=decision.symbol,
            timestamp=decision.timestamp,
            action=decision.action,
            quantity=decision.quantity,
            price=decision.price,
            fees=0.0,
            status="REJECTED",
            reason=reason,
        )
