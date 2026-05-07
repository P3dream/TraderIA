from __future__ import annotations

import statistics
from datetime import datetime

from traderia.config import AgentConfig
from traderia.models import Action, Decision, MarketBar, MarketContext, Position


class ContextBuilder:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def build(self, bars: list[MarketBar], sentiment_score: float, market_regime_score: float = 0.0) -> MarketContext | None:
        if len(bars) < self.config.min_history:
            return None

        closes = [bar.close for bar in bars]
        volumes = [bar.volume for bar in bars]
        short_ma = statistics.fmean(closes[-self.config.short_window :])
        long_ma = statistics.fmean(closes[-self.config.long_window :])
        returns = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes))]
        recent_returns = returns[-self.config.short_window :]
        volatility = statistics.pstdev(returns[-self.config.long_window :]) if len(returns) >= self.config.long_window else 0.0
        momentum = statistics.fmean(recent_returns)
        avg_volume = statistics.fmean(volumes[-self.config.long_window :])
        volume_ratio = volumes[-1] / avg_volume if avg_volume else 1.0
        trend_score = (short_ma / long_ma) - 1 if long_ma else 0.0
        timing_score = self._clamp((trend_score * 12) + (momentum * 20) - (volatility * 4), -1.0, 1.0)

        latest = bars[-1]
        return MarketContext(
            symbol=latest.symbol,
            timestamp=latest.timestamp,
            price=latest.close,
            short_ma=short_ma,
            long_ma=long_ma,
            momentum=momentum,
            volatility=volatility,
            volume_ratio=volume_ratio,
            sentiment_score=sentiment_score,
            timing_score=timing_score,
            market_regime_score=market_regime_score,
        )

    def _clamp(self, value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))


class TradingAgent:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.feedback_bias: dict[str, float] = {}
        self.last_exit_at: dict[str, datetime] = {}
        self.high_watermark: dict[str, float] = {}

    def load_feedback(self, biases: dict[str, float]) -> None:
        self.feedback_bias.update(biases)

    def decide(self, context: MarketContext, cash: float, position: Position | None) -> Decision:
        signal = (
            context.timing_score * 0.42
            + context.sentiment_score * 0.15
            + self._trend_strength(context) * 0.20
            + context.market_regime_score * 0.15
            + self.feedback_bias.get(context.symbol, 0.0) * 0.08
        )
        confidence = min(0.99, max(0.0, abs(signal)))
        action = Action.HOLD
        reason_parts = [
            f"signal={signal:.3f}",
            f"timing={context.timing_score:.3f}",
            f"regime={context.market_regime_score:.3f}",
            f"sentiment={context.sentiment_score:.3f}",
            f"momentum={context.momentum:.3f}",
            f"feedback={self.feedback_bias.get(context.symbol, 0.0):.3f}",
        ]

        if position:
            pnl_pct = (context.price / position.avg_price) - 1
            high = max(self.high_watermark.get(context.symbol, position.avg_price), context.price)
            self.high_watermark[context.symbol] = high
            trailing_drawdown = (context.price / high) - 1 if high else 0.0
            if pnl_pct <= -self.config.stop_loss_pct:
                action = Action.SELL
                confidence = max(confidence, 0.90)
                reason_parts.append("stop_loss")
            elif self.config.take_profit_pct > 0 and pnl_pct >= self.config.take_profit_pct:
                action = Action.SELL
                confidence = max(confidence, 0.82)
                reason_parts.append("take_profit")
            elif pnl_pct > 0 and trailing_drawdown <= -self.config.trailing_stop_pct:
                action = Action.SELL
                confidence = max(confidence, 0.86)
                reason_parts.append(f"trailing_stop high={high:.2f}")
            elif (
                self.config.momentum_exit_threshold > -1.0
                and context.momentum <= self.config.momentum_exit_threshold
                and context.timing_score < 0
            ):
                action = Action.SELL
                confidence = max(confidence, 0.78)
                reason_parts.append("momentum_breakdown")
            elif signal < -self.config.min_confidence_to_trade:
                action = Action.SELL
                reason_parts.append("negative_reversal")
        elif self._can_open_position(context, signal):
            action = Action.BUY
            reason_parts.append("positive_context")

        quantity = self._size_order(action, context.price, cash, position, confidence)
        if quantity == 0:
            action = Action.HOLD

        return Decision(
            symbol=context.symbol,
            timestamp=context.timestamp,
            action=action,
            confidence=confidence,
            quantity=quantity,
            price=context.price,
            reason=", ".join(reason_parts),
            expected_edge=signal,
        )

    def record_exit(self, symbol: str, timestamp: datetime) -> None:
        self.last_exit_at[symbol] = timestamp
        self.high_watermark.pop(symbol, None)

    def learn_from_feedback(self, symbol: str, realized_return: float) -> None:
        current = self.feedback_bias.get(symbol, 0.0)
        adjustment = max(-0.5, min(0.5, realized_return * 6))
        self.feedback_bias[symbol] = max(-1.0, min(1.0, current * 0.7 + adjustment * 0.3))

    def _trend_strength(self, context: MarketContext) -> float:
        if context.long_ma == 0:
            return 0.0
        return max(-1.0, min(1.0, ((context.short_ma / context.long_ma) - 1) * 10))

    def _size_order(self, action: Action, price: float, cash: float, position: Position | None, confidence: float) -> int:
        if action is Action.BUY:
            allocation = cash * self.config.max_position_pct * min(0.8, confidence)
            return max(0, int(allocation // price))
        if action is Action.SELL and position:
            return position.quantity
        return 0

    def _can_open_position(self, context: MarketContext, signal: float) -> bool:
        last_exit = self.last_exit_at.get(context.symbol)
        if last_exit is not None and (context.timestamp - last_exit).days < self.config.cooldown_days_after_exit:
            return False
        learned_penalty = abs(min(0.0, self.feedback_bias.get(context.symbol, 0.0))) * 0.35
        required_signal = self.config.min_confidence_to_trade + learned_penalty
        if signal <= required_signal:
            return False
        if context.timing_score < 0.45 or context.momentum <= 0:
            return False
        if context.sentiment_score < -0.25:
            return False
        if context.market_regime_score < self.config.min_market_regime_to_buy:
            return False
        if context.volatility > 0.04:
            return False
        return True
