from __future__ import annotations

from traderia.config import AgentConfig
from traderia.models import MarketContext


class ExposureEngine:
    """Converts market signals into a target overlay exposure."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def target_exposure(self, context: MarketContext) -> float:
        regime = self._clamp(context.market_regime_score, -1.0, 1.0)
        momentum = self._clamp(context.momentum * self.config.overlay_momentum_scale, -1.0, 1.0)
        sentiment = self._clamp(context.sentiment_score, -1.0, 1.0)
        volatility = self._volatility_penalty(context.volatility)

        exposure = (
            self.config.overlay_base_exposure
            + self.config.overlay_regime_weight * regime
            + self.config.overlay_momentum_weight * momentum
            + self.config.overlay_sentiment_weight * sentiment
            - self.config.overlay_volatility_weight * volatility
        )
        minimum = max(0.01, self.config.overlay_min_exposure)
        maximum = max(minimum, self.config.overlay_max_exposure)
        return self._clamp(exposure, minimum, maximum)

    def _volatility_penalty(self, volatility: float) -> float:
        if self.config.overlay_high_volatility <= 0:
            return 0.0
        return self._clamp(volatility / self.config.overlay_high_volatility, 0.0, 1.0)

    def _clamp(self, value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))
