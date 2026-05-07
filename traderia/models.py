from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class MarketContext:
    symbol: str
    timestamp: datetime
    price: float
    short_ma: float
    long_ma: float
    momentum: float
    volatility: float
    volume_ratio: float
    sentiment_score: float
    timing_score: float
    market_regime_score: float = 0.0
    rsi: float = 50.0
    macd_histogram: float = 0.0
    atr: float = 0.0
    bb_pct: float = 0.5


@dataclass(frozen=True)
class Decision:
    symbol: str
    timestamp: datetime
    action: Action
    confidence: float
    quantity: int
    price: float
    reason: str
    expected_edge: float


@dataclass(frozen=True)
class Order:
    symbol: str
    timestamp: datetime
    action: Action
    quantity: int
    price: float
    fees: float
    status: str
    reason: str


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    avg_price: float


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: datetime
    cash: float
    equity_value: float
    total_value: float


@dataclass(frozen=True)
class EffectivenessReport:
    starting_cash: float
    ending_value: float
    total_return: float
    total_return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    trades: int
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    benchmarks: tuple["BenchmarkReturn", ...] = ()


@dataclass(frozen=True)
class BenchmarkReturn:
    symbol: str
    starting_price: float
    ending_price: float
    total_return_pct: float
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0


@dataclass(frozen=True)
class DecisionExplanation:
    symbol: str
    timestamp: datetime
    action: Action
    confidence: float
    quantity: int
    price: float
    expected_edge: float
    reason: str
    timing_score: float
    market_regime_score: float
    sentiment_score: float
    momentum: float
    volatility: float
    volume_ratio: float
    short_ma: float
    long_ma: float
    order_status: str
    order_reason: str
    rsi: float = 50.0
    macd_histogram: float = 0.0
    atr: float = 0.0
    bb_pct: float = 0.5


@dataclass(frozen=True)
class AttributionRow:
    symbol: str
    exit_type: str
    trade_count: int
    avg_pnl: float
    avg_timing: float
    avg_sentiment: float
    avg_regime: float
    avg_momentum: float


@dataclass(frozen=True)
class WalkForwardFold:
    fold: int
    train_days: int
    test_days: int
    report: EffectivenessReport
