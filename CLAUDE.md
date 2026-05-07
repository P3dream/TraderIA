# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
# Run all tests
python -m unittest discover -s tests

# Run a single test
python -m unittest tests.test_agent.AgentTests.test_stop_loss_exit

# Synthetic smoke test (fast, no network)
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90

# Real market + news data
python -m traderia.cli simulate --market-provider yahoo --news-provider yahoo --symbols AAPL MSFT NVDA --days 90 --reset-db

# Overlay mode (tactical exposure on SPY)
python -m traderia.cli --db data\overlay.sqlite3 simulate --mode overlay --market-provider yahoo --news-provider yahoo --overlay-symbol SPY --days 251 --reset-db

# Walk-forward out-of-sample validation
python -m traderia.cli validate --symbols AAPL MSFT NVDA --total-days 500 --train-window 252 --test-window 63 --step 21

# Grid search to find optimal hyperparameters (ranked by Calmar ratio)
python -m traderia.cli optimize --symbols AAPL MSFT NVDA --days 252

# View results
python -m traderia.cli report
python -m traderia.cli explain --limit 20 --action BUY
python -m traderia.cli attribution
```

## Architecture

TraderIA is a **paper trading simulation** for US stocks. No real money is involved; all state lives in SQLite.

### Module responsibilities

| Module | Role |
|---|---|
| `config.py` | `AgentConfig` dataclass — all tunable parameters |
| `models.py` | Data classes: `MarketBar`, `MarketContext`, `Decision`, `Order`, `Position`, `PortfolioSnapshot`, `AttributionRow`, `WalkForwardFold` |
| `market.py` | Market data providers: `SyntheticMarketDataProvider`, `YahooChartMarketDataProvider` |
| `news.py` | News providers: `NeutralNewsProvider`, `SyntheticNewsProvider`, `YahooNewsProvider` |
| `sentiment.py` | `LexiconSentimentAnalyzer` (default), `HybridSentimentAnalyzer`, `ClaudeAPISentimentAnalyzer`, `OpenAIResponsesSentimentAnalyzer` |
| `strategy.py` | `ContextBuilder` (RSI, MACD, ATR, BB%, MAs) + `TradingAgent` (signal, Kelly sizing, ATR stop) |
| `broker.py` | `PaperBroker` — order execution with slippage/spread, position tracking, Herfindahl concentration |
| `exposure.py` | `ExposureEngine` — computes target exposure for overlay/growth-overlay modes |
| `storage.py` | `SQLiteStore` — WAL-mode SQLite, batched commits, persists all state |
| `metrics.py` | `effectiveness_report()` (Sharpe, Sortino, Calmar, drawdown, win rate), `attribution_report()`, `decision_explanations()` |
| `runner.py` | `PaperTradingRunner` — orchestrates the full simulation loop |
| `validation.py` | `walk_forward_validate()` — rolling out-of-sample validation across multiple folds |
| `optimizer.py` | `grid_search()` (Calmar-ranked), `fit_signal_weights()` (OLS regression), `build_feature_dataset()` |
| `cli.py` | CLI entry point: `simulate`, `validate`, `optimize`, `report`, `explain`, `attribution` |

### Decision formula

```
signal = (timing × w1) + (sentiment × w2) + (trend_strength × w3)
       + (market_regime × w4) + (feedback_bias × w5)
```

Default weights: `w1=0.42, w2=0.15, w3=0.20, w4=0.15, w5=0.08`

Weights are configurable via `AgentConfig` fields (`weight_timing`, `weight_sentiment`, `weight_trend`, `weight_regime`, `weight_feedback`) and can be fitted from data using `optimizer.fit_signal_weights(store)`.

- **timing** — ATR-normalised volatility + MA crossover + momentum
- **trend_strength** — short MA / long MA relationship (−1 to +1)
- **market_regime** — SPY/QQQ 50-bar trend; blocks buys below `-0.10`
- **feedback_bias** — penalty/boost from realized PnL on closed trades for that symbol
- **sentiment** — lexicon score from news headlines (−1 to +1)

### Technical indicators (computed in `ContextBuilder`)

| Indicator | Params | Field in `MarketContext` |
|---|---|---|
| Short/Long MA | `short_window=5`, `long_window=20` | `short_ma`, `long_ma` |
| RSI | `rsi_window=14` | `rsi` (50 = neutral; filter enabled via `rsi_overbought < 100`) |
| MACD histogram | EMA(12) − EMA(26) − signal_line | `macd_histogram` |
| ATR | `atr_window=14`, EMA-smoothed | `atr` (also replaces pstdev as volatility basis) |
| Bollinger %b | `long_window`-period, 2σ | `bb_pct` (0 = at lower band, 1 = at upper band) |

### Trading modes

1. **stock** (default) — picks individual symbols; manages stop loss / take profit / trailing stop / cooldown
2. **overlay** — tactical exposure on a single benchmark (SPY default); continuously rebalances between 30–120%
3. **growth-overlay** — rotates between growth symbols (SPY/QQQ) based on relative momentum

### Exit rules (stock mode)

- **Stop loss**: fixed `stop_loss_pct=3%` OR ATR-dynamic (`--use-atr-stop`: `entry − 2×ATR`)
- **Take profit**: disabled by default (`take_profit_pct = 0`)
- **Trailing stop**: 10% drawdown from peak while in profit
- **Momentum exit**: optional sell when momentum falls below threshold
- **Cooldown**: 7-day lockout after closing a position in a symbol

### Position sizing

Default: `cash × max_position_pct × min(0.8, confidence)`

Kelly (opt-in with `--use-kelly`): `cash × min(kelly_f × 0.25, max_position_pct)` where `kelly_f` is fed from the stored feedback history. Quarter-Kelly is used for robustness.

### Realistic execution costs

Applied in `broker.py` on every order:
- **BUY**: `exec_price = decision_price × (1 + slippage_pct + spread_pct / 2)`
- **SELL**: `exec_price = decision_price × (1 − slippage_pct − spread_pct / 2)`
- Defaults: `slippage_pct=0.001`, `spread_pct=0.0005`, `fee_pct=0.0005`

### Portfolio risk tools (`broker.py`)

- `portfolio_concentration_score(prices)` — Herfindahl index (0 = diversified, 1 = 100% in one name)
- `correlation_penalty(histories)` — average pairwise correlation among held symbols

### Walk-forward validation

`walk_forward_validate(config, symbols, total_days=500, train_window=252, test_window=63, step=21)` runs isolated folds, each with a fresh agent and temporary DB. Each fold tests on out-of-sample data. Meaningful with `--market-provider yahoo` (real calendar slices). Synthetic data produces identical folds by design.

### Grid search

`grid_search(config, symbols, days)` sweeps:
- `min_confidence_to_trade`: [0.40, 0.50, 0.60]
- `trailing_stop_pct`: [0.06, 0.08, 0.10, 0.12]
- `stop_loss_pct`: [0.02, 0.03, 0.04]
- `max_position_pct`: [0.15, 0.20, 0.25]

Ranked by **Calmar ratio** (annualised return / max drawdown). ~108 trials, ~15s on synthetic data.

### Data flow

```
Market history → News headlines → Sentiment scoring
→ ContextBuilder (MAs, RSI, MACD, ATR, BB%, momentum, volatility, regime)
→ TradingAgent (signal + exit checks + Kelly sizing)
→ PaperBroker (slippage/spread execution, positions)
→ SQLiteStore (WAL-mode, batched commits)
→ metrics.py (Sharpe, Sortino, Calmar, attribution)
```

### Database schema

Default path: `data/traderia.sqlite3`. Tables: `market_contexts`, `decisions`, `orders`, `portfolio_snapshots`, `feedback_events`, `benchmark_snapshots`.

`market_contexts` stores: price, MAs, momentum, volatility (ATR-based), volume_ratio, sentiment, timing_score, market_regime_score, **rsi**, **macd_histogram**, **atr**, **bb_pct**.

Use `--reset-db` to wipe and restart; `--reset-runs` keeps learned feedback but clears trading history.

### Storage performance

WAL journal mode + `synchronous=NORMAL`. Commits are batched once per simulation day (in `save_snapshot`), not per row. Single 3-symbol 60-day simulation: ~0.1s.

### Testing strategy

`tests/test_agent.py` runs the full agent stack with synthetic data — no network calls, no external dependencies. Tests cover: broker logic, decision rules, cooldown enforcement, feedback learning, regime blocking, stop loss, trailing stop, overlay margin, sentiment providers, news providers, benchmarks.

Smoke testing uses `SyntheticMarketDataProvider`; performance evaluation uses `YahooChartMarketDataProvider`.
