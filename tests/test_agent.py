from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import unittest

from traderia.config import AgentConfig
from traderia.broker import PaperBroker
from traderia.exposure import ExposureEngine
from traderia.market import (
    SyntheticMarketDataProvider,
    YahooChartMarketDataProvider,
    build_market_provider,
)
from traderia.metrics import decision_explanations
from traderia.models import Action, Decision, MarketBar, MarketContext, Order, Position
from traderia.news import NeutralNewsProvider, SyntheticNewsProvider, YahooNewsProvider, build_news_provider
from traderia.runner import PaperTradingRunner
from traderia.sentiment import HybridSentimentAnalyzer, OpenAIResponsesSentimentAnalyzer, build_sentiment_analyzer
from traderia.storage import SQLiteStore
from traderia.strategy import ContextBuilder, TradingAgent


def make_bars(symbol: str = "TEST") -> list[MarketBar]:
    start = datetime(2026, 1, 1, 16)
    bars = []
    price = 100.0
    for day in range(30):
        price *= 1.01
        bars.append(MarketBar(symbol, start + timedelta(days=day), price - 1, price + 1, price - 2, price, 1_000_000))
    return bars


def remove_sqlite_artifacts(db_path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        path = Path(f"{db_path}{suffix}")
        if path.exists():
            path.unlink()


class AgentTests(unittest.TestCase):
    def test_agent_buys_positive_context(self) -> None:
        config = AgentConfig(min_confidence_to_trade=0.2)
        context = ContextBuilder(config).build(make_bars(), sentiment_score=1.0)
        self.assertIsNotNone(context)

        decision = TradingAgent(config).decide(context, cash=100_000, position=None)

        self.assertIs(decision.action, Action.BUY)
        self.assertGreater(decision.quantity, 0)

    def test_context_requires_minimum_history(self) -> None:
        config = AgentConfig(min_history=25)

        context = ContextBuilder(config).build(make_bars()[:10], sentiment_score=0.0)

        self.assertIsNone(context)

    def test_agent_uses_cooldown_after_exit(self) -> None:
        config = AgentConfig(min_confidence_to_trade=0.2, cooldown_days_after_exit=7)
        context = ContextBuilder(config).build(make_bars(), sentiment_score=1.0)
        self.assertIsNotNone(context)
        agent = TradingAgent(config)
        agent.record_exit("TEST", context.timestamp - timedelta(days=2))

        decision = agent.decide(context, cash=100_000, position=None)

        self.assertIs(decision.action, Action.HOLD)

    def test_agent_penalizes_bad_feedback(self) -> None:
        config = AgentConfig(min_confidence_to_trade=0.62)
        context = ContextBuilder(config).build(make_bars(), sentiment_score=1.0)
        self.assertIsNotNone(context)
        agent = TradingAgent(config)
        agent.load_feedback({"TEST": -1.0})

        decision = agent.decide(context, cash=100_000, position=None)

        self.assertIs(decision.action, Action.HOLD)

    def test_agent_blocks_buy_when_market_regime_is_weak(self) -> None:
        config = AgentConfig(min_confidence_to_trade=0.2, min_market_regime_to_buy=-0.1)
        context = ContextBuilder(config).build(make_bars(), sentiment_score=1.0)
        self.assertIsNotNone(context)
        context = replace(context, market_regime_score=-0.5)

        decision = TradingAgent(config).decide(context, cash=100_000, position=None)

        self.assertIs(decision.action, Action.HOLD)
        self.assertIn("regime=-0.500", decision.reason)

    def test_agent_sells_on_trailing_stop(self) -> None:
        config = AgentConfig(take_profit_pct=0.20, trailing_stop_pct=0.04)
        agent = TradingAgent(config)
        position = Position("TEST", quantity=10, avg_price=100.0)
        first_context = MarketContext(
            symbol="TEST",
            timestamp=datetime(2026, 1, 30, 16),
            price=110.0,
            short_ma=108.0,
            long_ma=100.0,
            momentum=0.01,
            volatility=0.02,
            volume_ratio=1.0,
            sentiment_score=0.0,
            timing_score=0.4,
        )
        second_context = MarketContext(
            symbol="TEST",
            timestamp=datetime(2026, 1, 31, 16),
            price=105.0,
            short_ma=107.0,
            long_ma=100.0,
            momentum=-0.01,
            volatility=0.02,
            volume_ratio=1.0,
            sentiment_score=0.0,
            timing_score=0.1,
        )
        agent.decide(first_context, cash=100_000, position=position)

        decision = agent.decide(second_context, cash=100_000, position=position)

        self.assertIs(decision.action, Action.SELL)
        self.assertIn("trailing_stop", decision.reason)

    def test_agent_sells_on_momentum_breakdown(self) -> None:
        config = AgentConfig(take_profit_pct=0.0, trailing_stop_pct=0.10, momentum_exit_threshold=-0.005)
        agent = TradingAgent(config)
        position = Position("TEST", quantity=10, avg_price=100.0)
        context = MarketContext(
            symbol="TEST",
            timestamp=datetime(2026, 1, 31, 16),
            price=101.0,
            short_ma=100.0,
            long_ma=102.0,
            momentum=-0.006,
            volatility=0.02,
            volume_ratio=1.0,
            sentiment_score=0.0,
            timing_score=-0.05,
            market_regime_score=0.8,
        )

        decision = agent.decide(context, cash=100_000, position=position)

        self.assertIs(decision.action, Action.SELL)
        self.assertIn("momentum_breakdown", decision.reason)

    def test_exposure_engine_uses_continuous_nonzero_exposure(self) -> None:
        engine = ExposureEngine(AgentConfig(overlay_min_exposure=0.30, overlay_max_exposure=1.20))
        weak_context = MarketContext(
            symbol="SPY",
            timestamp=datetime(2026, 1, 31, 16),
            price=100.0,
            short_ma=98.0,
            long_ma=100.0,
            momentum=-0.02,
            volatility=0.05,
            volume_ratio=1.0,
            sentiment_score=0.0,
            timing_score=-0.2,
            market_regime_score=-1.0,
        )
        strong_context = replace(weak_context, momentum=0.03, volatility=0.005, market_regime_score=1.0)

        weak_exposure = engine.target_exposure(weak_context)
        strong_exposure = engine.target_exposure(strong_context)

        self.assertGreaterEqual(weak_exposure, 0.30)
        self.assertGreater(strong_exposure, 1.0)
        self.assertLessEqual(strong_exposure, 1.20)

    def test_exposure_engine_uses_sentiment_as_small_tilt(self) -> None:
        engine = ExposureEngine(AgentConfig(overlay_sentiment_weight=0.10))
        neutral_context = MarketContext(
            symbol="SPY",
            timestamp=datetime(2026, 1, 31, 16),
            price=100.0,
            short_ma=100.0,
            long_ma=100.0,
            momentum=0.0,
            volatility=0.01,
            volume_ratio=1.0,
            sentiment_score=0.0,
            timing_score=0.0,
            market_regime_score=0.0,
        )

        negative = engine.target_exposure(replace(neutral_context, sentiment_score=-1.0))
        neutral = engine.target_exposure(neutral_context)
        positive = engine.target_exposure(replace(neutral_context, sentiment_score=1.0))

        self.assertLess(negative, neutral)
        self.assertGreater(positive, neutral)

    def test_overlay_broker_allows_margin_within_exposure_cap(self) -> None:
        broker = PaperBroker(AgentConfig(mode="overlay", overlay_max_exposure=1.20))
        decision = Decision(
            symbol="SPY",
            timestamp=datetime(2026, 1, 31, 16),
            action=Action.BUY,
            confidence=0.9,
            quantity=1100,
            price=100.0,
            reason="overlay target=1.10",
            expected_edge=0.9,
        )

        order = broker.execute(decision)

        self.assertEqual(order.status, "FILLED")
        self.assertLess(broker.cash, 0)

    def test_growth_overlay_broker_allows_margin_within_exposure_cap(self) -> None:
        broker = PaperBroker(AgentConfig(mode="growth-overlay", overlay_max_exposure=1.20))
        decision = Decision(
            symbol="QQQ",
            timestamp=datetime(2026, 1, 31, 16),
            action=Action.BUY,
            confidence=0.9,
            quantity=1100,
            price=100.0,
            reason="growth overlay target=1.10",
            expected_edge=0.9,
        )

        order = broker.execute(decision)

        self.assertEqual(order.status, "FILLED")
        self.assertLess(broker.cash, 0)

    def test_openai_sentiment_falls_back_without_key(self) -> None:
        analyzer = OpenAIResponsesSentimentAnalyzer(api_key="")

        score = analyzer.score(["TEST faces weak demand and downgrade risk"])

        self.assertLess(score, 0)

    def test_builds_openai_sentiment_provider_alias(self) -> None:
        analyzer = build_sentiment_analyzer("codex", "gpt-5")

        self.assertIsInstance(analyzer, OpenAIResponsesSentimentAnalyzer)

    def test_hybrid_uses_lexicon_for_obvious_headline(self) -> None:
        class RaisingLLM:
            def score(self, headlines: list[str]) -> float:
                raise AssertionError("LLM should not be called")

        analyzer = HybridSentimentAnalyzer(llm=RaisingLLM())

        score = analyzer.score(["TEST reports strong growth and record profit"])

        self.assertGreater(score, 0)

    def test_hybrid_escalates_ambiguous_headline(self) -> None:
        class FakeLLM:
            def score(self, headlines: list[str]) -> float:
                return -0.42

        analyzer = HybridSentimentAnalyzer(llm=FakeLLM())

        score = analyzer.score(["TEST beats estimates but guidance disappoints investors"])

        self.assertEqual(score, -0.42)

    def test_builds_hybrid_sentiment_provider(self) -> None:
        analyzer = build_sentiment_analyzer("hybrid", "gpt-5")

        self.assertIsInstance(analyzer, HybridSentimentAnalyzer)

    def test_builds_synthetic_market_provider_by_default(self) -> None:
        provider = build_market_provider("synthetic")

        self.assertIsInstance(provider, SyntheticMarketDataProvider)

    def test_builds_yahoo_market_provider(self) -> None:
        provider = build_market_provider("yahoo")

        self.assertIsInstance(provider, YahooChartMarketDataProvider)

    def test_yahoo_parses_payload(self) -> None:
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1767225600, 1767312000],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [100, 101],
                                    "high": [110, 111],
                                    "low": [95, 96],
                                    "close": [108, 109],
                                    "volume": [123456, 234567],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }
        provider = YahooChartMarketDataProvider()

        bars = provider._parse_payload("AAPL", payload)

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1].close, 109)
        self.assertEqual(bars[-1].symbol, "AAPL")

    def test_builds_news_providers(self) -> None:
        self.assertIsInstance(build_news_provider("synthetic"), SyntheticNewsProvider)
        self.assertIsInstance(build_news_provider("yahoo"), YahooNewsProvider)
        self.assertIsInstance(build_news_provider("none"), NeutralNewsProvider)

    def test_runner_uses_real_news_with_real_market_auto(self) -> None:
        db_path = Path("data/runner_news_test.sqlite3")
        remove_sqlite_artifacts(db_path)
        try:
            runner = PaperTradingRunner(AgentConfig(db_path=str(db_path), market_provider="yahoo", news_provider="auto"))

            self.assertEqual(runner._resolve_news_provider(runner.config), "yahoo")
            runner.store.connection.close()
        finally:
            remove_sqlite_artifacts(db_path)

    def test_runner_uses_synthetic_news_only_with_synthetic_market_auto(self) -> None:
        db_path = Path("data/runner_news_test.sqlite3")
        remove_sqlite_artifacts(db_path)
        try:
            runner = PaperTradingRunner(AgentConfig(db_path=str(db_path), market_provider="synthetic", news_provider="auto"))

            self.assertEqual(runner._resolve_news_provider(runner.config), "synthetic")
            runner.store.connection.close()
        finally:
            remove_sqlite_artifacts(db_path)

    def test_decision_explanations_join_context_and_order(self) -> None:
        db_path = Path("data/explain_test.sqlite3")
        remove_sqlite_artifacts(db_path)
        try:
            store = SQLiteStore(str(db_path))
            timestamp = datetime(2026, 1, 30, 16)
            store.save_context(
                MarketContext(
                    symbol="TEST",
                    timestamp=timestamp,
                    price=123.0,
                    short_ma=120.0,
                    long_ma=115.0,
                    momentum=0.012,
                    volatility=0.02,
                    volume_ratio=1.3,
                    sentiment_score=0.4,
                    timing_score=0.7,
                    market_regime_score=0.25,
                )
            )
            store.save_decision(
                Decision(
                    symbol="TEST",
                    timestamp=timestamp,
                    action=Action.BUY,
                    confidence=0.73,
                    quantity=10,
                    price=123.0,
                    reason="signal=0.730, positive_context",
                    expected_edge=0.73,
                )
            )
            store.save_order(
                Order(
                    symbol="TEST",
                    timestamp=timestamp,
                    action=Action.BUY,
                    quantity=10,
                    price=123.0,
                    fees=0.61,
                    status="FILLED",
                    reason="signal=0.730, positive_context",
                )
            )

            explanations = decision_explanations(store)
            store.connection.close()
        finally:
            remove_sqlite_artifacts(db_path)

        self.assertEqual(len(explanations), 1)
        self.assertEqual(explanations[0].symbol, "TEST")
        self.assertIs(explanations[0].action, Action.BUY)
        self.assertEqual(explanations[0].order_status, "FILLED")
        self.assertAlmostEqual(explanations[0].timing_score, 0.7)
        self.assertAlmostEqual(explanations[0].market_regime_score, 0.25)
        self.assertAlmostEqual(explanations[0].sentiment_score, 0.4)

    def test_runner_records_benchmarks(self) -> None:
        db_path = Path("data/benchmark_test.sqlite3")
        remove_sqlite_artifacts(db_path)
        try:
            runner = PaperTradingRunner(
                AgentConfig(
                    db_path=str(db_path),
                    market_provider="synthetic",
                    news_provider="none",
                    benchmark_symbols=("SPY", "QQQ"),
                )
            )
            runner.simulate(["AAPL"], 30)

            report = runner.report()
            runner.store.connection.close()
        finally:
            remove_sqlite_artifacts(db_path)

        self.assertEqual([benchmark.symbol for benchmark in report.benchmarks], ["QQQ", "SPY"])
        self.assertNotEqual(report.benchmarks[0].total_return_pct, 0.0)

    def test_overlay_mode_rebalances_benchmark_exposure(self) -> None:
        db_path = Path("data/overlay_test.sqlite3")
        remove_sqlite_artifacts(db_path)
        try:
            runner = PaperTradingRunner(
                AgentConfig(
                    db_path=str(db_path),
                    mode="overlay",
                    market_provider="synthetic",
                    news_provider="none",
                    overlay_symbol="SPY",
                    benchmark_symbols=("SPY", "QQQ"),
                    market_regime_window=20,
                )
            )
            runner.simulate(["IGNORED"], 70)

            decisions = runner.store.rows("SELECT action FROM decisions")
            report = runner.report()
            runner.store.connection.close()
        finally:
            remove_sqlite_artifacts(db_path)

        self.assertGreater(len(decisions), 0)
        self.assertIn("SPY", [benchmark.symbol for benchmark in report.benchmarks])
        self.assertIsInstance(report.sharpe_ratio, float)

    def test_growth_overlay_selects_stronger_relative_momentum(self) -> None:
        runner = PaperTradingRunner(
            AgentConfig(
                db_path="data/growth_selector_test.sqlite3",
                mode="growth-overlay",
                growth_symbols=("SPY", "QQQ"),
                growth_momentum_window=3,
            )
        )
        start = datetime(2026, 1, 1, 16)
        histories = {
            "SPY": [
                MarketBar("SPY", start + timedelta(days=index), 100, 101, 99, close, 1_000_000)
                for index, close in enumerate([100, 101, 102, 103])
            ],
            "QQQ": [
                MarketBar("QQQ", start + timedelta(days=index), 100, 101, 99, close, 1_000_000)
                for index, close in enumerate([100, 102, 105, 110])
            ],
        }
        try:
            symbol = runner._select_overlay_symbol(histories, ["SPY", "QQQ"], 3)
            runner.store.connection.close()
        finally:
            remove_sqlite_artifacts(Path("data/growth_selector_test.sqlite3"))

        self.assertEqual(symbol, "QQQ")

    def test_overlay_buy_sizes_to_margin_cap(self) -> None:
        runner = PaperTradingRunner(
            AgentConfig(
                db_path="data/overlay_sizing_test.sqlite3",
                mode="growth-overlay",
                overlay_max_exposure=1.20,
                overlay_min_rebalance_pct=0.0,
            )
        )
        try:
            context = MarketContext(
                symbol="QQQ",
                timestamp=datetime(2026, 1, 31, 16),
                price=100.0,
                short_ma=100.0,
                long_ma=100.0,
                momentum=0.02,
                volatility=0.005,
                volume_ratio=1.0,
                sentiment_score=1.0,
                timing_score=0.5,
                market_regime_score=1.0,
            )
            decision = runner._overlay_decision(context)
            runner.store.connection.close()
        finally:
            remove_sqlite_artifacts(Path("data/overlay_sizing_test.sqlite3"))

        self.assertEqual(decision.action, Action.BUY)
        self.assertLessEqual(decision.quantity * decision.price, 120_000)


if __name__ == "__main__":
    unittest.main()
