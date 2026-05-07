from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentConfig:
    starting_cash: float = 100_000.0
    max_position_pct: float = 0.20
    min_confidence_to_trade: float = 0.50
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.0
    trailing_stop_pct: float = 0.10
    momentum_exit_threshold: float = -1.0
    fee_pct: float = 0.0005
    slippage_pct: float = 0.001
    spread_pct: float = 0.0005
    short_window: int = 5
    long_window: int = 20
    min_history: int = 25
    cooldown_days_after_exit: int = 7
    db_path: str = "data/traderia.sqlite3"
    sentiment_provider: str = "lexicon"
    sentiment_model: str = "gpt-5"
    market_provider: str = "synthetic"
    news_provider: str = "auto"
    yahoo_cache_dir: str = "data/yahoo_cache"
    benchmark_symbols: tuple[str, ...] = field(default_factory=lambda: ("SPY", "QQQ"))
    market_regime_window: int = 50
    min_market_regime_to_buy: float = -0.10
    mode: str = "stock"
    overlay_symbol: str = "SPY"
    overlay_full_regime: float = 0.25
    overlay_cash_regime: float = -0.25
    overlay_neutral_exposure: float = 0.50
    overlay_min_rebalance_pct: float = 0.10
    overlay_min_exposure: float = 0.30
    overlay_max_exposure: float = 1.20
    overlay_base_exposure: float = 0.70
    overlay_regime_weight: float = 0.35
    overlay_momentum_weight: float = 0.20
    overlay_sentiment_weight: float = 0.05
    overlay_volatility_weight: float = 0.15
    overlay_momentum_scale: float = 25.0
    overlay_high_volatility: float = 0.025
    growth_symbols: tuple[str, ...] = field(default_factory=lambda: ("SPY", "QQQ"))
    growth_momentum_window: int = 60
    growth_switch_margin: float = 0.02
    # signal weights (Fase 3.2 — can be overridden by optimizer)
    weight_timing: float = 0.42
    weight_sentiment: float = 0.15
    weight_trend: float = 0.20
    weight_regime: float = 0.15
    weight_feedback: float = 0.08
    # ATR-based dynamic stop loss (Fase 2.3 / 5.3)
    use_atr_stop: bool = False
    atr_stop_multiplier: float = 2.0
    atr_window: int = 14
    # Kelly criterion position sizing (Fase 5.2)
    use_kelly: bool = False
    kelly_fraction: float = 0.25
    # RSI thresholds (Fase 2.1) — rsi_overbought=100 disables the filter (default)
    rsi_overbought: float = 100.0
    rsi_oversold: float = 0.0
    rsi_window: int = 14
    # correlation cap (Fase 5.1)
    max_portfolio_concentration: float = 0.70
