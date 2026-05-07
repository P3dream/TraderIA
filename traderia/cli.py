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

    # ------------------------------------------------------------------ simulate
    simulate = subparsers.add_parser("simulate", help="Run a paper trading simulation")
    simulate.add_argument("--mode", choices=["stock", "overlay", "growth-overlay"], default="stock")
    simulate.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT", "NVDA"])
    simulate.add_argument("--overlay-symbol", default="SPY")
    simulate.add_argument("--growth-symbols", nargs="+", default=["SPY", "QQQ"])
    simulate.add_argument("--benchmark-symbols", nargs="+", default=["SPY", "QQQ"])
    simulate.add_argument("--days", type=int, default=90)
    simulate.add_argument("--cash", type=float, default=100_000.0)
    simulate.add_argument("--min-confidence", type=float, default=0.50)
    simulate.add_argument("--max-position-pct", type=float, default=0.20)
    simulate.add_argument("--stop-loss-pct", type=float, default=0.03)
    simulate.add_argument("--take-profit-pct", type=float, default=0.0)
    simulate.add_argument("--trailing-stop-pct", type=float, default=0.10)
    simulate.add_argument("--momentum-exit-threshold", type=float, default=-1.0)
    simulate.add_argument("--market-regime-window", type=int, default=50)
    simulate.add_argument("--overlay-full-regime", type=float, default=0.25)
    simulate.add_argument("--overlay-cash-regime", type=float, default=-0.25)
    simulate.add_argument("--overlay-neutral-exposure", type=float, default=0.50)
    simulate.add_argument("--overlay-min-rebalance-pct", type=float, default=0.10)
    simulate.add_argument("--overlay-min-exposure", type=float, default=0.30)
    simulate.add_argument("--overlay-max-exposure", type=float, default=1.20)
    simulate.add_argument("--overlay-base-exposure", type=float, default=0.70)
    simulate.add_argument("--overlay-regime-weight", type=float, default=0.35)
    simulate.add_argument("--overlay-momentum-weight", type=float, default=0.20)
    simulate.add_argument("--overlay-sentiment-weight", type=float, default=0.05)
    simulate.add_argument("--overlay-volatility-weight", type=float, default=0.15)
    simulate.add_argument("--overlay-momentum-scale", type=float, default=25.0)
    simulate.add_argument("--overlay-high-volatility", type=float, default=0.025)
    simulate.add_argument("--growth-momentum-window", type=int, default=60)
    simulate.add_argument("--growth-switch-margin", type=float, default=0.02)
    simulate.add_argument("--min-market-regime", type=float, default=-0.10)
    simulate.add_argument("--slippage-pct", type=float, default=0.001, help="Slippage per trade as decimal (default 0.1%%)")
    simulate.add_argument("--spread-pct", type=float, default=0.0005, help="Bid-ask spread cost per trade as decimal")
    simulate.add_argument("--use-atr-stop", action="store_true", help="Use ATR-based dynamic stop loss instead of fixed %%")
    simulate.add_argument("--atr-stop-multiplier", type=float, default=2.0, help="ATR multiplier for dynamic stop loss")
    simulate.add_argument("--use-kelly", action="store_true", help="Use quarter-Kelly position sizing")
    simulate.add_argument(
        "--market-provider",
        choices=["synthetic", "yahoo", "yahoo-chart", "yfinance"],
        default="synthetic",
    )
    simulate.add_argument(
        "--news-provider",
        choices=["auto", "none", "neutral", "synthetic", "yahoo"],
        default="auto",
    )
    simulate.add_argument(
        "--sentiment-provider",
        choices=["lexicon", "hybrid", "hybrid-claude", "claude", "openai", "codex", "llm"],
        default="lexicon",
    )
    simulate.add_argument("--sentiment-model", default="gpt-5")
    simulate.add_argument("--reset-db", action="store_true")
    simulate.add_argument("--reset-runs", action="store_true")

    # ------------------------------------------------------------------ validate
    validate = subparsers.add_parser("validate", help="Walk-forward out-of-sample validation")
    validate.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT", "NVDA"])
    validate.add_argument("--total-days", type=int, default=500)
    validate.add_argument("--train-window", type=int, default=252)
    validate.add_argument("--test-window", type=int, default=63)
    validate.add_argument("--step", type=int, default=21)

    # ------------------------------------------------------------------ optimize
    optimize = subparsers.add_parser("optimize", help="Grid search over key hyperparameters")
    optimize.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT", "NVDA"])
    optimize.add_argument("--days", type=int, default=252)

    # ------------------------------------------------------------------ report / explain / attribution
    subparsers.add_parser("report", help="Show effectiveness metrics")
    explain = subparsers.add_parser("explain", help="Show decision-level explainability")
    explain.add_argument("--limit", type=int, default=20)
    explain.add_argument("--action", choices=["BUY", "SELL", "HOLD"])
    subparsers.add_parser("attribution", help="Show P&L attribution by exit type and symbol")

    args = parser.parse_args()
    config = _build_config(args)

    if getattr(args, "reset_db", False) or getattr(args, "reset_runs", False):
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
        SQLiteStore(args.db).reset_trading_history(clear_feedback=not getattr(args, "reset_runs", False))

    if args.command == "simulate":
        runner = PaperTradingRunner(config)
        try:
            runner.simulate(args.symbols, args.days)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        report = runner.report()
        print_report(report)
        return

    if args.command == "validate":
        from traderia.validation import print_walk_forward_summary, walk_forward_validate
        folds = walk_forward_validate(
            config,
            symbols=args.symbols,
            total_days=args.total_days,
            train_window=args.train_window,
            test_window=args.test_window,
            step=args.step,
        )
        print_walk_forward_summary(folds)
        return

    if args.command == "optimize":
        from traderia.optimizer import grid_search
        best = grid_search(config, symbols=args.symbols, days=args.days, verbose=True)
        print(f"\nOptimal config applied — re-run simulate with these flags:")
        print(f"  --min-confidence {best.min_confidence_to_trade:.2f}")
        print(f"  --trailing-stop-pct {best.trailing_stop_pct:.3f}")
        print(f"  --stop-loss-pct {best.stop_loss_pct:.3f}")
        print(f"  --max-position-pct {best.max_position_pct:.3f}")
        return

    runner = PaperTradingRunner(config)

    if args.command == "report":
        print_report(runner.report())
        return

    if args.command == "explain":
        print_explanations(runner.explain(limit=args.limit, action=args.action))
        return

    if args.command == "attribution":
        from traderia.metrics import attribution_report
        rows = attribution_report(runner.store)
        print_attribution(rows)


def _build_config(args) -> AgentConfig:
    return AgentConfig(
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
        slippage_pct=getattr(args, "slippage_pct", 0.001),
        spread_pct=getattr(args, "spread_pct", 0.0005),
        use_atr_stop=getattr(args, "use_atr_stop", False),
        atr_stop_multiplier=getattr(args, "atr_stop_multiplier", 2.0),
        use_kelly=getattr(args, "use_kelly", False),
    )


def print_report(report) -> None:
    print("TraderIA effectiveness report")
    print(f"Starting cash:     ${report.starting_cash:,.2f}")
    print(f"Ending value:      ${report.ending_value:,.2f}")
    print(f"Total return:      ${report.total_return:,.2f}")
    print(f"Total return pct:  {report.total_return_pct:.2f}%")
    print(f"Max drawdown:      {report.max_drawdown_pct:.2f}%")
    print(f"Sharpe ratio:      {report.sharpe_ratio:.2f}")
    print(f"Sortino ratio:     {report.sortino_ratio:.2f}")
    print(f"Calmar ratio:      {report.calmar_ratio:.3f}")
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
        print(f"RSI:               {explanation.rsi:.1f}")
        print(f"MACD histogram:    {explanation.macd_histogram:+.4f}")
        print(f"ATR:               {explanation.atr:.4f}")
        print(f"BB%:               {explanation.bb_pct:.3f}")
        print(f"Volatility:        {explanation.volatility:.4f}")
        print(f"Volume ratio:      {explanation.volume_ratio:.2f}")
        print(f"MA short/long:     {explanation.short_ma:.2f} / {explanation.long_ma:.2f}")
        print(f"Order status:      {explanation.order_status}")
        if explanation.order_reason and explanation.order_reason != explanation.reason:
            print(f"Order reason:      {explanation.order_reason}")
        print(f"Decision reason:   {explanation.reason}")


def print_attribution(rows) -> None:
    print("TraderIA P&L Attribution Report")
    if not rows:
        print("No closed trades found.")
        return
    print(f"\n{'Symbol':<8}  {'Exit Type':<22}  {'#':>4}  {'AvgP&L':>10}  {'AvgTiming':>9}  {'AvgSentim':>9}  {'AvgRegime':>9}")
    print("-" * 85)
    for row in rows:
        print(
            f"{row.symbol:<8}  {row.exit_type:<22}  {row.trade_count:>4}  "
            f"${row.avg_pnl:>9.2f}  {row.avg_timing:>+9.3f}  "
            f"{row.avg_sentiment:>+9.3f}  {row.avg_regime:>+9.3f}"
        )


if __name__ == "__main__":
    main()
