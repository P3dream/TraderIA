from __future__ import annotations

import argparse
import sys
from pathlib import Path

from traderia.config import AgentConfig
from traderia.runner import PaperTradingRunner
from traderia.storage import SQLiteStore


def main() -> None:
    parser = argparse.ArgumentParser(description="TraderIA paper trading agent")
    parser.add_argument("--db", default="data/traderia.sqlite3", help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    simulate = subparsers.add_parser("simulate", help="Run a paper trading simulation")
    simulate.add_argument("--mode", choices=["stock", "overlay", "growth-overlay"], default="stock")
    simulate.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT", "NVDA"])
    simulate.add_argument("--overlay-symbol", default="SPY")
    simulate.add_argument("--growth-symbols", nargs="+", default=["SPY", "QQQ"])
    simulate.add_argument("--benchmark-symbols", nargs="+", default=["SPY", "QQQ"])
    simulate.add_argument("--days", type=int, default=90)
    simulate.add_argument("--cash", type=float, default=100_000.0)
    simulate.add_argument("--min-confidence", type=float, default=0.50, help="Minimum signal required to open a position")
    simulate.add_argument("--max-position-pct", type=float, default=0.20, help="Maximum cash allocation per position")
    simulate.add_argument("--stop-loss-pct", type=float, default=0.03, help="Stop loss percentage as decimal")
    simulate.add_argument("--take-profit-pct", type=float, default=0.0, help="Take profit percentage as decimal; 0 disables it")
    simulate.add_argument("--trailing-stop-pct", type=float, default=0.10, help="Trailing stop percentage as decimal")
    simulate.add_argument(
        "--momentum-exit-threshold",
        type=float,
        default=-1.0,
        help="Sell when held asset momentum falls below this threshold and timing is negative; -1 disables it",
    )
    simulate.add_argument("--market-regime-window", type=int, default=50, help="Moving average window for SPY/QQQ regime")
    simulate.add_argument("--overlay-full-regime", type=float, default=0.25, help="Regime score for full overlay exposure")
    simulate.add_argument("--overlay-cash-regime", type=float, default=-0.25, help="Regime score for cash overlay exposure")
    simulate.add_argument("--overlay-neutral-exposure", type=float, default=0.50, help="Neutral overlay exposure")
    simulate.add_argument("--overlay-min-rebalance-pct", type=float, default=0.10, help="Minimum exposure gap to rebalance")
    simulate.add_argument("--overlay-min-exposure", type=float, default=0.30, help="Minimum overlay exposure; must stay above zero")
    simulate.add_argument("--overlay-max-exposure", type=float, default=1.20, help="Maximum overlay exposure")
    simulate.add_argument("--overlay-base-exposure", type=float, default=0.70, help="Base continuous overlay exposure")
    simulate.add_argument("--overlay-regime-weight", type=float, default=0.35, help="Continuous exposure weight for market regime")
    simulate.add_argument("--overlay-momentum-weight", type=float, default=0.20, help="Continuous exposure weight for price momentum")
    simulate.add_argument("--overlay-sentiment-weight", type=float, default=0.05, help="Continuous exposure weight for news sentiment")
    simulate.add_argument("--overlay-volatility-weight", type=float, default=0.15, help="Continuous exposure penalty for volatility")
    simulate.add_argument("--overlay-momentum-scale", type=float, default=25.0, help="Multiplier used to normalize price momentum")
    simulate.add_argument("--overlay-high-volatility", type=float, default=0.025, help="Volatility level treated as high risk")
    simulate.add_argument("--growth-momentum-window", type=int, default=60, help="Lookback window for growth overlay relative momentum")
    simulate.add_argument("--growth-switch-margin", type=float, default=0.02, help="Minimum relative momentum gap required to switch growth asset")
    simulate.add_argument(
        "--min-market-regime",
        type=float,
        default=-0.10,
        help="Minimum SPY/QQQ regime score required to open positions",
    )
    simulate.add_argument(
        "--market-provider",
        choices=["synthetic", "yahoo", "yahoo-chart", "yfinance"],
        default="synthetic",
        help="Market data source",
    )
    simulate.add_argument(
        "--news-provider",
        choices=["auto", "none", "neutral", "synthetic", "yahoo"],
        default="auto",
        help="News source for sentiment. auto uses synthetic only with synthetic market data, and yahoo with real data.",
    )
    simulate.add_argument(
        "--sentiment-provider",
        choices=["lexicon", "hybrid", "openai", "codex", "llm"],
        default="lexicon",
        help="Sentiment analyzer to use",
    )
    simulate.add_argument("--sentiment-model", default="gpt-5", help="OpenAI model for LLM sentiment")
    simulate.add_argument("--reset-db", action="store_true", help="Clear saved paper-trading history before running")
    simulate.add_argument(
        "--reset-runs",
        action="store_true",
        help="Clear runs before simulating but keep feedback memory for learning",
    )

    subparsers.add_parser("report", help="Show effectiveness metrics")
    explain = subparsers.add_parser("explain", help="Show decision-level explainability")
    explain.add_argument("--limit", type=int, default=20, help="Maximum number of decisions to show")
    explain.add_argument("--action", choices=["BUY", "SELL", "HOLD"], help="Filter explanations by action")

    args = parser.parse_args()
    config = AgentConfig(
        starting_cash=getattr(args, "cash", 100_000.0),
        db_path=args.db,
        sentiment_provider=getattr(args, "sentiment_provider", "lexicon"),
        sentiment_model=getattr(args, "sentiment_model", "gpt-5"),
        market_provider=getattr(args, "market_provider", "synthetic"),
        news_provider=getattr(args, "news_provider", "auto"),
        min_confidence_to_trade=getattr(args, "min_confidence", 0.50),
        max_position_pct=getattr(args, "max_position_pct", 0.20),
        stop_loss_pct=getattr(args, "stop_loss_pct", 0.03),
        take_profit_pct=getattr(args, "take_profit_pct", 0.0),
        trailing_stop_pct=getattr(args, "trailing_stop_pct", 0.10),
        momentum_exit_threshold=getattr(args, "momentum_exit_threshold", -1.0),
        benchmark_symbols=tuple(getattr(args, "benchmark_symbols", ["SPY", "QQQ"])),
        market_regime_window=getattr(args, "market_regime_window", 50),
        min_market_regime_to_buy=getattr(args, "min_market_regime", -0.10),
        mode=getattr(args, "mode", "stock"),
        overlay_symbol=getattr(args, "overlay_symbol", "SPY"),
        overlay_full_regime=getattr(args, "overlay_full_regime", 0.25),
        overlay_cash_regime=getattr(args, "overlay_cash_regime", -0.25),
        overlay_neutral_exposure=getattr(args, "overlay_neutral_exposure", 0.50),
        overlay_min_rebalance_pct=getattr(args, "overlay_min_rebalance_pct", 0.10),
        overlay_min_exposure=max(0.01, getattr(args, "overlay_min_exposure", 0.30)),
        overlay_max_exposure=getattr(args, "overlay_max_exposure", 1.20),
        overlay_base_exposure=getattr(args, "overlay_base_exposure", 0.70),
        overlay_regime_weight=getattr(args, "overlay_regime_weight", 0.35),
        overlay_momentum_weight=getattr(args, "overlay_momentum_weight", 0.20),
        overlay_sentiment_weight=getattr(args, "overlay_sentiment_weight", 0.05),
        overlay_volatility_weight=getattr(args, "overlay_volatility_weight", 0.15),
        overlay_momentum_scale=getattr(args, "overlay_momentum_scale", 25.0),
        overlay_high_volatility=getattr(args, "overlay_high_volatility", 0.025),
        growth_symbols=tuple(getattr(args, "growth_symbols", ["SPY", "QQQ"])),
        growth_momentum_window=getattr(args, "growth_momentum_window", 60),
        growth_switch_margin=getattr(args, "growth_switch_margin", 0.02),
    )
    if getattr(args, "reset_db", False) or getattr(args, "reset_runs", False):
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
        SQLiteStore(args.db).reset_trading_history(clear_feedback=not getattr(args, "reset_runs", False))
    runner = PaperTradingRunner(config)

    if args.command == "simulate":
        try:
            runner.simulate(args.symbols, args.days)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        report = runner.report()
        print_report(report)
        return

    if args.command == "report":
        print_report(runner.report())
        return

    if args.command == "explain":
        print_explanations(runner.explain(limit=args.limit, action=args.action))


def print_report(report) -> None:
    print("TraderIA effectiveness report")
    print(f"Starting cash:     ${report.starting_cash:,.2f}")
    print(f"Ending value:      ${report.ending_value:,.2f}")
    print(f"Total return:      ${report.total_return:,.2f}")
    print(f"Total return pct:  {report.total_return_pct:.2f}%")
    print(f"Max drawdown:      {report.max_drawdown_pct:.2f}%")
    print(f"Sharpe ratio:      {report.sharpe_ratio:.2f}")
    print(f"Sortino ratio:     {report.sortino_ratio:.2f}")
    print(f"Win rate:          {report.win_rate_pct:.2f}%")
    print(f"Profit factor:     {report.profit_factor:.2f}")
    print(f"Closed trades:     {report.trades}")
    if report.benchmarks:
        print("Benchmarks:")
        for benchmark in report.benchmarks:
            print(
                f"  {benchmark.symbol}:          {benchmark.total_return_pct:+.2f}% "
                f"DD {benchmark.max_drawdown_pct:.2f}% Sharpe {benchmark.sharpe_ratio:.2f} "
                f"(${benchmark.starting_price:,.2f} -> ${benchmark.ending_price:,.2f})"
            )


def print_explanations(explanations) -> None:
    print("TraderIA decision explanations")
    if not explanations:
        print("No decisions found.")
        return

    for explanation in explanations:
        print()
        print(f"{explanation.timestamp.date()} {explanation.symbol} {explanation.action.value}")
        print(f"Price:             ${explanation.price:,.2f}")
        print(f"Quantity:          {explanation.quantity}")
        print(f"Confidence:        {explanation.confidence:.2f}")
        print(f"Expected edge:     {explanation.expected_edge:+.3f}")
        print(f"Timing score:      {explanation.timing_score:+.3f}")
        print(f"Market regime:     {explanation.market_regime_score:+.3f}")
        print(f"Sentiment score:   {explanation.sentiment_score:+.3f}")
        print(f"Momentum:          {explanation.momentum:+.4f}")
        print(f"Volatility:        {explanation.volatility:.4f}")
        print(f"Volume ratio:      {explanation.volume_ratio:.2f}")
        print(f"MA short/long:     {explanation.short_ma:.2f} / {explanation.long_ma:.2f}")
        print(f"Order status:      {explanation.order_status}")
        if explanation.order_reason and explanation.order_reason != explanation.reason:
            print(f"Order reason:      {explanation.order_reason}")
        print(f"Decision reason:   {explanation.reason}")


if __name__ == "__main__":
    main()
