from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from traderia.config import AgentConfig
from traderia.models import Action, Decision, Order, PortfolioSnapshot, Position


class PaperBroker:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.cash = config.starting_cash
        self.positions: dict[str, Position] = {}
        self.realized_pnl: list[float] = []

    def execute(self, decision: Decision) -> Order:
        if decision.action is Action.HOLD or decision.quantity <= 0:
            return Order(
                symbol=decision.symbol,
                timestamp=decision.timestamp,
                action=Action.HOLD,
                quantity=0,
                price=decision.price,
                fees=0.0,
                status="SKIPPED",
                reason=decision.reason,
            )

        fees = decision.price * decision.quantity * self.config.fee_pct
        gross = decision.price * decision.quantity

        if decision.action is Action.BUY:
            total = gross + fees
            if total > self.cash and not self._allows_overlay_margin(decision, gross):
                return self._rejected(decision, "insufficient cash")

            current = self.positions.get(decision.symbol, Position(decision.symbol, 0, 0.0))
            new_qty = current.quantity + decision.quantity
            avg_price = ((current.avg_price * current.quantity) + gross) / new_qty
            self.positions[decision.symbol] = Position(decision.symbol, new_qty, avg_price)
            self.cash -= total
            return self._filled(decision, fees)

        current = self.positions.get(decision.symbol)
        if current is None or current.quantity < decision.quantity:
            return self._rejected(decision, "insufficient shares")

        pnl = (decision.price - current.avg_price) * decision.quantity - fees
        self.realized_pnl.append(pnl)
        remaining = current.quantity - decision.quantity
        if remaining == 0:
            del self.positions[decision.symbol]
        else:
            self.positions[decision.symbol] = replace(current, quantity=remaining)
        self.cash += gross - fees
        return self._filled(decision, fees)

    def snapshot(self, timestamp: datetime, prices: dict[str, float]) -> PortfolioSnapshot:
        equity = 0.0
        for symbol, position in self.positions.items():
            equity += position.quantity * prices.get(symbol, position.avg_price)
        return PortfolioSnapshot(timestamp=timestamp, cash=self.cash, equity_value=equity, total_value=self.cash + equity)

    def position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def _allows_overlay_margin(self, decision: Decision, gross: float) -> bool:
        if self.config.mode not in {"overlay", "growth-overlay"}:
            return False
        current = self.positions.get(decision.symbol, Position(decision.symbol, 0, 0.0))
        current_notional = current.quantity * decision.price
        portfolio_value = self.cash + current_notional
        if portfolio_value <= 0:
            return False
        resulting_notional = current_notional + gross
        return resulting_notional <= portfolio_value * self.config.overlay_max_exposure

    def _filled(self, decision: Decision, fees: float) -> Order:
        return Order(
            symbol=decision.symbol,
            timestamp=decision.timestamp,
            action=decision.action,
            quantity=decision.quantity,
            price=decision.price,
            fees=fees,
            status="FILLED",
            reason=decision.reason,
        )

    def _rejected(self, decision: Decision, reason: str) -> Order:
        return Order(
            symbol=decision.symbol,
            timestamp=decision.timestamp,
            action=decision.action,
            quantity=decision.quantity,
            price=decision.price,
            fees=0.0,
            status="REJECTED",
            reason=reason,
        )
