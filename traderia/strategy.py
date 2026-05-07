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
        short_ma = statistics.fmean(closes[-self.config.short_window:])
        long_ma = statistics.fmean(closes[-self.config.long_window:])
        returns = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes))]
        recent_returns = returns[-self.config.short_window:]
        momentum = statistics.fmean(recent_returns)
        avg_volume = statistics.fmean(volumes[-self.config.long_window:])
        volume_ratio = volumes[-1] / avg_volume if avg_volume else 1.0

        atr = self._calc_atr(bars)
        price = bars[-1].close
        # ATR-normalised volatility is more stable than pstdev of returns
        volatility = (atr / price) if price and atr else statistics.pstdev(returns[-self.config.long_window:]) if len(returns) >= self.config.long_window else 0.0

        trend_score = (short_ma / long_ma) - 1 if long_ma else 0.0
        timing_score = self._clamp((trend_score * 12) + (momentum * 20) - (volatility * 4), -1.0, 1.0)

        rsi = self._calc_rsi(closes)
        macd_histogram = self._calc_macd_histogram(closes)
        bb_pct = self._calc_bb_pct(closes, long_ma)

        latest = bars[-1]
        return MarketContext(
            symbol=latest.symbol,
            timestamp=latest.timestamp,
            price=price,
            short_ma=short_ma,
            long_ma=long_ma,
            momentum=momentum,
            volatility=volatility,
            volume_ratio=volume_ratio,
            sentiment_score=sentiment_score,
            timing_score=timing_score,
            market_regime_score=market_regime_score,
            rsi=rsi,
            macd_histogram=macd_histogram,
            atr=atr,
            bb_pct=bb_pct,
        )

    def _calc_atr(self, bars: list[MarketBar]) -> float:
        window = self.config.atr_window
        if len(bars) < 2:
            return 0.0
        true_ranges: list[float] = []
        for i in range(1, len(bars)):
            hl = bars[i].high - bars[i].low
            hc = abs(bars[i].high - bars[i - 1].close)
            lc = abs(bars[i].low - bars[i - 1].close)
            true_ranges.append(max(hl, hc, lc))
        return self._ema(true_ranges, window)

    def _calc_rsi(self, closes: list[float]) -> float:
        window = self.config.rsi_window
        if len(closes) < window + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        recent = deltas[-window:]
        gains = [max(0.0, d) for d in recent]
        losses = [abs(min(0.0, d)) for d in recent]
        avg_gain = statistics.fmean(gains)
        avg_loss = statistics.fmean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _calc_macd_histogram(self, closes: list[float]) -> float:
        if len(closes) < 26:
            return 0.0
        ema12 = self._ema(closes, 12)
        ema26 = self._ema(closes, 26)
        macd_line = ema12 - ema26
        # approximate signal line as EMA(9) of the last few MACD values
        if len(closes) < 35:
            return macd_line
        macd_series: list[float] = []
        for end in range(len(closes) - 8, len(closes) + 1):
            slice_ = closes[:end]
            if len(slice_) >= 26:
                macd_series.append(self._ema(slice_, 12) - self._ema(slice_, 26))
        if not macd_series:
            return macd_line
        signal_line = self._ema(macd_series, min(9, len(macd_series)))
        return macd_line - signal_line

    def _calc_bb_pct(self, closes: list[float], long_ma: float) -> float:
        window = self.config.long_window
        if len(closes) < window:
            return 0.5
        recent = closes[-window:]
        std = statistics.pstdev(recent)
        if std == 0:
            return 0.5
        upper = long_ma + 2 * std
        lower = long_ma - 2 * std
        band = upper - lower
        if band == 0:
            return 0.5
        return max(0.0, min(1.0, (closes[-1] - lower) / band))

    def _ema(self, values: list[float], period: int) -> float:
        if not values:
            return 0.0
        k = 2.0 / (period + 1)
        result = values[0]
        for v in values[1:]:
            result = v * k + result * (1.0 - k)
        return result

    def _clamp(self, value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))


class TradingAgent:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.feedback_bias: dict[str, float] = {}
        self.last_exit_at: dict[str, datetime] = {}
        self.high_watermark: dict[str, float] = {}
        self.atr_at_entry: dict[str, float] = {}
        # Kelly stats loaded from store
        self._kelly_win_rate: float = 0.5
        self._kelly_avg_win: float = 0.01
        self._kelly_avg_loss: float = 0.01

    def load_feedback(self, biases: dict[str, float]) -> None:
        self.feedback_bias.update(biases)

    def load_kelly_stats(self, win_rate: float, avg_win: float, avg_loss: float) -> None:
        self._kelly_win_rate = max(0.01, min(0.99, win_rate))
        self._kelly_avg_win = max(0.0001, avg_win)
        self._kelly_avg_loss = max(0.0001, avg_loss)

    def decide(self, context: MarketContext, cash: float, position: Position | None) -> Decision:
        signal = (
            context.timing_score * self.config.weight_timing
            + context.sentiment_score * self.config.weight_sentiment
            + self._trend_strength(context) * self.config.weight_trend
            + context.market_regime_score * self.config.weight_regime
            + self.feedback_bias.get(context.symbol, 0.0) * self.config.weight_feedback
        )
        confidence = min(0.99, max(0.0, abs(signal)))
        action = Action.HOLD
        reason_parts = [
            f"signal={signal:.3f}",
            f"timing={context.timing_score:.3f}",
            f"regime={context.market_regime_score:.3f}",
            f"sentiment={context.sentiment_score:.3f}",
            f"momentum={context.momentum:.3f}",
            f"rsi={context.rsi:.1f}",
            f"feedback={self.feedback_bias.get(context.symbol, 0.0):.3f}",
        ]

        if position:
            pnl_pct = (context.price / position.avg_price) - 1
            high = max(self.high_watermark.get(context.symbol, position.avg_price), context.price)
            self.high_watermark[context.symbol] = high
            trailing_drawdown = (context.price / high) - 1 if high else 0.0

            stop_triggered = self._check_stop_loss(context, position, pnl_pct)
            if stop_triggered:
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

        if action is Action.BUY:
            self.atr_at_entry[context.symbol] = context.atr

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
        self.atr_at_entry.pop(symbol, None)

    def learn_from_feedback(self, symbol: str, realized_return: float) -> None:
        current = self.feedback_bias.get(symbol, 0.0)
        adjustment = max(-0.5, min(0.5, realized_return * 6))
        self.feedback_bias[symbol] = max(-1.0, min(1.0, current * 0.7 + adjustment * 0.3))

    def _check_stop_loss(self, context: MarketContext, position: Position, pnl_pct: float) -> bool:
        if self.config.use_atr_stop:
            atr_entry = self.atr_at_entry.get(context.symbol)
            if atr_entry and atr_entry > 0:
                stop_price = position.avg_price - self.config.atr_stop_multiplier * atr_entry
                return context.price <= stop_price
        return pnl_pct <= -self.config.stop_loss_pct

    def _trend_strength(self, context: MarketContext) -> float:
        if context.long_ma == 0:
            return 0.0
        return max(-1.0, min(1.0, ((context.short_ma / context.long_ma) - 1) * 10))

    def _size_order(self, action: Action, price: float, cash: float, position: Position | None, confidence: float) -> int:
        if action is Action.BUY:
            if self.config.use_kelly:
                allocation = cash * self._kelly_position_pct(confidence)
            else:
                allocation = cash * self.config.max_position_pct * min(0.8, confidence)
            return max(0, int(allocation // price))
        if action is Action.SELL and position:
            return position.quantity
        return 0

    def _kelly_position_pct(self, confidence: float) -> float:
        w = self._kelly_win_rate
        loss_rate = 1.0 - w
        avg_win = self._kelly_avg_win
        avg_loss = self._kelly_avg_loss
        if avg_win == 0:
            return 0.0
        kelly_f = (w * avg_win - loss_rate * avg_loss) / avg_win
        # quarter-Kelly for robustness to estimation noise
        raw = kelly_f * self.config.kelly_fraction * min(1.0, confidence / 0.6)
        return max(0.0, min(self.config.max_position_pct, raw))

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
        # RSI overbought filter — avoid chasing extended moves
        if context.rsi > self.config.rsi_overbought:
            return False
        return True
