from __future__ import annotations

import statistics

from traderia.broker import PaperBroker
from traderia.config import AgentConfig
from traderia.exposure import ExposureEngine
from traderia.models import Action, Decision, MarketBar
from traderia.market import build_market_provider
from traderia.metrics import decision_explanations, effectiveness_report
from traderia.news import build_news_provider
from traderia.sentiment import build_sentiment_analyzer
from traderia.storage import SQLiteStore
from traderia.strategy import ContextBuilder, TradingAgent


class PaperTradingRunner:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.store = SQLiteStore(config.db_path)
        self.market = build_market_provider(
            config.market_provider,
            yahoo_cache_dir=config.yahoo_cache_dir,
        )
        self.news = build_news_provider(self._resolve_news_provider(config))
        self.sentiment = build_sentiment_analyzer(config.sentiment_provider, config.sentiment_model)
        self.context_builder = ContextBuilder(config)
        self.agent = TradingAgent(config)
        self.agent.load_feedback(self.store.feedback_biases())
        self.exposure_engine = ExposureEngine(config)
        self.broker = PaperBroker(config)

    def simulate(self, symbols: list[str], days: int) -> None:
        if self.config.mode in {"overlay", "growth-overlay"}:
            self._simulate_overlay(days)
            return

        trade_symbols = [symbol.upper() for symbol in symbols]
        benchmark_symbols = [symbol.upper() for symbol in self.config.benchmark_symbols]
        histories = self.market.history(list(dict.fromkeys(trade_symbols + benchmark_symbols)), days)
        benchmark_bases = {
            symbol: histories[symbol][0].close
            for symbol in benchmark_symbols
            if histories.get(symbol) and histories[symbol][0].close
        }
        for day_index in range(days):
            prices: dict[str, float] = {}
            timestamp = None
            for symbol in trade_symbols:
                bars = histories[symbol][: day_index + 1]
                latest = bars[-1]
                timestamp = latest.timestamp
                prices[symbol] = latest.close

                headlines = self.news.headlines(symbol, day_index)
                sentiment_score = self.sentiment.score(headlines)
                market_regime_score = self._market_regime_score(histories, benchmark_symbols, day_index)
                context = self.context_builder.build(bars, sentiment_score, market_regime_score=market_regime_score)
                if context is None:
                    continue

                self.store.save_context(context)
                decision = self.agent.decide(context, self.broker.cash, self.broker.position(symbol))
                decision_id = self.store.save_decision(decision)
                order = self.broker.execute(decision)
                self.store.save_order(order)

                if order.status == "FILLED" and order.action.value == "SELL":
                    self.agent.record_exit(symbol, order.timestamp)
                    realized_return = self.broker.realized_pnl[-1] / max(1.0, order.price * order.quantity)
                    self.agent.learn_from_feedback(symbol, realized_return)
                    self.store.save_feedback(
                        timestamp=order.timestamp.isoformat(),
                        symbol=symbol,
                        decision_id=decision_id,
                        realized_return=realized_return,
                        note="closed paper position",
                    )

            if timestamp is not None:
                self.store.save_snapshot(self.broker.snapshot(timestamp, prices))
                self._record_benchmarks(histories, benchmark_bases, day_index)

    def report(self):
        return effectiveness_report(self.store, self.config)

    def explain(self, limit: int = 20, action: str | None = None):
        return decision_explanations(self.store, limit=limit, action=action)

    def _simulate_overlay(self, days: int) -> None:
        overlay_symbol = self.config.overlay_symbol.upper()
        overlay_symbols = [overlay_symbol]
        if self.config.mode == "growth-overlay":
            overlay_symbols = [symbol.upper() for symbol in self.config.growth_symbols]
        benchmark_symbols = list(dict.fromkeys([*overlay_symbols, *[symbol.upper() for symbol in self.config.benchmark_symbols]]))
        histories = self.market.history(benchmark_symbols, days)
        benchmark_bases = {
            symbol: histories[symbol][0].close
            for symbol in benchmark_symbols
            if histories.get(symbol) and histories[symbol][0].close
        }

        for day_index in range(days):
            overlay_symbol = self._select_overlay_symbol(histories, overlay_symbols, day_index)
            bars = histories[overlay_symbol][: day_index + 1]
            latest = bars[-1]
            prices = {symbol: histories[symbol][day_index].close for symbol in overlay_symbols}
            market_regime_score = self._market_regime_score(histories, benchmark_symbols, day_index)
            headlines = self.news.headlines(overlay_symbol, day_index)
            sentiment_score = self.sentiment.score(headlines)
            context = self.context_builder.build(bars, sentiment_score=sentiment_score, market_regime_score=market_regime_score)
            if context is not None:
                self.store.save_context(context)
                for symbol in overlay_symbols:
                    if symbol != overlay_symbol:
                        exit_decision = self._exit_non_selected_overlay(symbol, latest.timestamp, prices[symbol])
                        if exit_decision is not None:
                            self.store.save_decision(exit_decision)
                            self.store.save_order(self.broker.execute(exit_decision))
                decision = self._overlay_decision(context)
                self.store.save_decision(decision)
                order = self.broker.execute(decision)
                self.store.save_order(order)

            self.store.save_snapshot(self.broker.snapshot(latest.timestamp, prices))
            self._record_benchmarks(histories, benchmark_bases, day_index)

    def _select_overlay_symbol(self, histories: dict[str, list[MarketBar]], symbols: list[str], day_index: int) -> str:
        if self.config.mode != "growth-overlay" or len(symbols) <= 1:
            return symbols[0]
        scores = {symbol: self._relative_momentum(histories[symbol][: day_index + 1]) for symbol in symbols}
        current_symbols = [symbol for symbol in symbols if self.broker.position(symbol)]
        current = current_symbols[0] if current_symbols else symbols[0]
        best = max(symbols, key=lambda symbol: scores[symbol])
        if best != current and scores[best] - scores[current] < self.config.growth_switch_margin:
            return current
        return best

    def _relative_momentum(self, bars: list[MarketBar]) -> float:
        if len(bars) < 2:
            return 0.0
        window = min(self.config.growth_momentum_window, len(bars) - 1)
        start_price = bars[-window - 1].close
        if not start_price:
            return 0.0
        return (bars[-1].close / start_price) - 1

    def _exit_non_selected_overlay(self, symbol: str, timestamp, price: float) -> Decision | None:
        position = self.broker.position(symbol)
        if position is None:
            return None
        return Decision(
            symbol=symbol,
            timestamp=timestamp,
            action=Action.SELL,
            confidence=0.99,
            quantity=position.quantity,
            price=price,
            reason="growth_overlay_switch",
            expected_edge=0.0,
        )

    def _overlay_decision(self, context) -> Decision:
        target_exposure = self.exposure_engine.target_exposure(context)
        portfolio_value = self.broker.cash
        position = self.broker.position(context.symbol)
        if position:
            portfolio_value += position.quantity * context.price
        current_value = 0.0 if position is None else position.quantity * context.price
        current_exposure = current_value / portfolio_value if portfolio_value else 0.0
        exposure_gap = target_exposure - current_exposure
        reason = (
            f"overlay target={target_exposure:.2f}, current={current_exposure:.2f}, "
            f"regime={context.market_regime_score:.3f}, momentum={context.momentum:.3f}, "
            f"volatility={context.volatility:.3f}"
        )

        if abs(exposure_gap) < self.config.overlay_min_rebalance_pct:
            return Decision(
                symbol=context.symbol,
                timestamp=context.timestamp,
                action=Action.HOLD,
                confidence=abs(context.market_regime_score),
                quantity=0,
                price=context.price,
                reason=reason,
                expected_edge=context.market_regime_score,
            )

        if exposure_gap > 0:
            current_notional = current_value
            max_notional = portfolio_value * self.config.overlay_max_exposure
            affordable_notional = self.broker.cash + max(0.0, max_notional - current_notional)
            buy_notional = min(portfolio_value * exposure_gap, max(0.0, affordable_notional))
            quantity = int(buy_notional // context.price)
            action = Action.BUY
        else:
            quantity = min(position.quantity if position else 0, int((portfolio_value * abs(exposure_gap)) // context.price))
            action = Action.SELL

        if quantity <= 0:
            action = Action.HOLD
        return Decision(
            symbol=context.symbol,
            timestamp=context.timestamp,
            action=action,
            confidence=min(0.99, abs(exposure_gap)),
            quantity=quantity,
            price=context.price,
            reason=reason,
            expected_edge=context.market_regime_score,
        )

    def _record_benchmarks(
        self, histories: dict[str, list[MarketBar]], benchmark_bases: dict[str, float], day_index: int
    ) -> None:
        for symbol, base_price in benchmark_bases.items():
            benchmark_bar = histories[symbol][day_index]
            benchmark_value = self.config.starting_cash * (benchmark_bar.close / base_price)
            self.store.save_benchmark_snapshot(
                timestamp=benchmark_bar.timestamp.isoformat(),
                symbol=symbol,
                price=benchmark_bar.close,
                total_value=benchmark_value,
            )

    def _market_regime_score(
        self, histories: dict[str, list[MarketBar]], benchmark_symbols: list[str], day_index: int
    ) -> float:
        scores: list[float] = []
        for symbol in benchmark_symbols:
            bars = histories.get(symbol, [])[: day_index + 1]
            if len(bars) < self.config.market_regime_window:
                continue
            closes = [bar.close for bar in bars]
            regime_ma = statistics.fmean(closes[-self.config.market_regime_window :])
            trend = (closes[-1] / regime_ma) - 1 if regime_ma else 0.0
            momentum_window = min(20, len(closes) - 1)
            momentum = (closes[-1] / closes[-momentum_window - 1]) - 1 if momentum_window > 0 else 0.0
            scores.append(max(-1.0, min(1.0, trend * 8 + momentum * 3)))
        if not scores:
            return 0.0
        return max(-1.0, min(1.0, statistics.fmean(scores)))

    def _resolve_news_provider(self, config: AgentConfig) -> str:
        if config.news_provider != "auto":
            return config.news_provider
        if config.market_provider == "synthetic":
            return "synthetic"
        return "yahoo"
