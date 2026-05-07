from __future__ import annotations

import sqlite3
from pathlib import Path

from traderia.models import Decision, MarketContext, Order, PortfolioSnapshot


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.migrate()

    def migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                price REAL NOT NULL,
                short_ma REAL NOT NULL,
                long_ma REAL NOT NULL,
                momentum REAL NOT NULL,
                volatility REAL NOT NULL,
                volume_ratio REAL NOT NULL,
                sentiment_score REAL NOT NULL,
                timing_score REAL NOT NULL,
                market_regime_score REAL NOT NULL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                reason TEXT NOT NULL,
                expected_edge REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                fees REAL NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                cash REAL NOT NULL,
                equity_value REAL NOT NULL,
                total_value REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feedback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                decision_id INTEGER,
                realized_return REAL NOT NULL,
                note TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS benchmark_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                total_value REAL NOT NULL
            );
            """
        )
        self._ensure_column("market_contexts", "market_regime_score", "REAL NOT NULL DEFAULT 0.0")
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def save_context(self, context: MarketContext) -> None:
        self.connection.execute(
            """
            INSERT INTO market_contexts (
                symbol, timestamp, price, short_ma, long_ma, momentum,
                volatility, volume_ratio, sentiment_score, timing_score, market_regime_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context.symbol,
                context.timestamp.isoformat(),
                context.price,
                context.short_ma,
                context.long_ma,
                context.momentum,
                context.volatility,
                context.volume_ratio,
                context.sentiment_score,
                context.timing_score,
                context.market_regime_score,
            ),
        )
        self.connection.commit()

    def save_decision(self, decision: Decision) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO decisions (
                symbol, timestamp, action, confidence, quantity, price, reason, expected_edge
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.symbol,
                decision.timestamp.isoformat(),
                decision.action.value,
                decision.confidence,
                decision.quantity,
                decision.price,
                decision.reason,
                decision.expected_edge,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def save_order(self, order: Order) -> None:
        self.connection.execute(
            """
            INSERT INTO orders (
                symbol, timestamp, action, quantity, price, fees, status, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.symbol,
                order.timestamp.isoformat(),
                order.action.value,
                order.quantity,
                order.price,
                order.fees,
                order.status,
                order.reason,
            ),
        )
        self.connection.commit()

    def save_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        self.connection.execute(
            """
            INSERT INTO portfolio_snapshots (timestamp, cash, equity_value, total_value)
            VALUES (?, ?, ?, ?)
            """,
            (
                snapshot.timestamp.isoformat(),
                snapshot.cash,
                snapshot.equity_value,
                snapshot.total_value,
            ),
        )
        self.connection.commit()

    def save_benchmark_snapshot(self, timestamp: str, symbol: str, price: float, total_value: float) -> None:
        self.connection.execute(
            """
            INSERT INTO benchmark_snapshots (timestamp, symbol, price, total_value)
            VALUES (?, ?, ?, ?)
            """,
            (timestamp, symbol, price, total_value),
        )
        self.connection.commit()

    def save_feedback(self, timestamp: str, symbol: str, decision_id: int, realized_return: float, note: str) -> None:
        self.connection.execute(
            """
            INSERT INTO feedback_events (timestamp, symbol, decision_id, realized_return, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp, symbol, decision_id, realized_return, note),
        )
        self.connection.commit()

    def feedback_biases(self) -> dict[str, float]:
        rows = self.rows(
            """
            SELECT symbol, AVG(realized_return) AS avg_return, COUNT(*) AS samples
            FROM feedback_events
            GROUP BY symbol
            """
        )
        biases: dict[str, float] = {}
        for row in rows:
            samples = int(row["samples"])
            confidence = min(1.0, samples / 2)
            biases[str(row["symbol"])] = max(-1.0, min(1.0, float(row["avg_return"]) * 12 * confidence))
        return biases

    def reset_trading_history(self, clear_feedback: bool = True) -> None:
        self.connection.executescript(
            """
            DELETE FROM market_contexts;
            DELETE FROM decisions;
            DELETE FROM orders;
            DELETE FROM portfolio_snapshots;
            DELETE FROM benchmark_snapshots;
            """
        )
        if clear_feedback:
            self.connection.execute("DELETE FROM feedback_events")
        self.connection.commit()

    def rows(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(query, params))
