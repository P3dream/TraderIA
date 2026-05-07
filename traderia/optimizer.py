from __future__ import annotations

import math
import statistics
import tempfile
from dataclasses import replace
from pathlib import Path

from traderia.config import AgentConfig
from traderia.models import EffectivenessReport
from traderia.runner import PaperTradingRunner
from traderia.storage import SQLiteStore


# ---------------------------------------------------------------------------
# Feature dataset
# ---------------------------------------------------------------------------

def build_feature_dataset(store: SQLiteStore) -> list[dict]:
    """Build a table of (features, next_day_return) from stored contexts.

    Each row represents one market-context observation joined with the
    next-day price change — the supervision signal for weight regression.
    """
    rows = store.rows(
        """
        SELECT
            c.symbol,
            c.timestamp,
            c.timing_score,
            c.sentiment_score,
            c.market_regime_score,
            c.momentum,
            c.short_ma,
            c.long_ma,
            LEAD(c.price, 1) OVER (PARTITION BY c.symbol ORDER BY c.timestamp) AS next_price,
            c.price AS current_price
        FROM market_contexts c
        ORDER BY c.symbol, c.timestamp
        """
    )
    dataset: list[dict] = []
    for row in rows:
        if row["next_price"] is None or row["current_price"] is None or float(row["current_price"]) == 0:
            continue
        next_return = (float(row["next_price"]) / float(row["current_price"])) - 1
        long_ma = float(row["long_ma"])
        short_ma = float(row["short_ma"])
        trend_strength = max(-1.0, min(1.0, ((short_ma / long_ma) - 1) * 10)) if long_ma else 0.0
        dataset.append({
            "symbol": str(row["symbol"]),
            "timestamp": str(row["timestamp"]),
            "timing": float(row["timing_score"]),
            "sentiment": float(row["sentiment_score"]),
            "trend": trend_strength,
            "regime": float(row["market_regime_score"]),
            "momentum": float(row["momentum"]),
            "next_return": next_return,
        })
    return dataset


# ---------------------------------------------------------------------------
# Linear regression weight fitting  (closed-form: w = (X^T X)^-1 X^T y)
# ---------------------------------------------------------------------------

def fit_signal_weights(store: SQLiteStore) -> dict[str, float]:
    """Fit signal component weights via ordinary least squares.

    Solves: minimise sum((w·x - y)^2)  where y = next_day_return.
    Returns a dict of normalised positive weights summing to 1.0,
    or the hardcoded defaults if there is insufficient data.
    """
    dataset = build_feature_dataset(store)
    if len(dataset) < 30:
        return {"timing": 0.42, "sentiment": 0.15, "trend": 0.20, "regime": 0.15, "feedback": 0.08}

    feature_keys = ["timing", "sentiment", "trend", "regime", "momentum"]
    n = len(dataset)
    k = len(feature_keys)

    # Build X (n×k) and y (n×1)
    X: list[list[float]] = [[row[key] for key in feature_keys] for row in dataset]
    y: list[float] = [row["next_return"] for row in dataset]

    # X^T X  (k×k)
    XtX = [[0.0] * k for _ in range(k)]
    for row in X:
        for i in range(k):
            for j in range(k):
                XtX[i][j] += row[i] * row[j]

    # X^T y  (k×1)
    Xty = [0.0] * k
    for idx, row in enumerate(X):
        for i in range(k):
            Xty[i] += row[i] * y[idx]

    # Solve via Gaussian elimination with regularisation (ridge λ=1e-4)
    lam = 1e-4
    for i in range(k):
        XtX[i][i] += lam

    w = _solve_linear(XtX, Xty, k)
    if w is None:
        return {"timing": 0.42, "sentiment": 0.15, "trend": 0.20, "regime": 0.15, "feedback": 0.08}

    # Assign positive contributions only; renormalise
    raw = {key: max(0.0, w[i]) for i, key in enumerate(feature_keys)}
    total = sum(raw.values()) or 1.0
    normalised = {key: val / total for key, val in raw.items()}
    # feedback is not in context table — assign remainder proportionally
    factor = 0.92  # reserve 8% for feedback as before
    result = {key: val * factor for key, val in normalised.items()}
    result["feedback"] = 1.0 - sum(result.values())
    return result


def _solve_linear(A: list[list[float]], b: list[float], n: int) -> list[float] | None:
    """Gaussian elimination with partial pivoting. Returns solution vector or None."""
    mat = [A[i][:] + [b[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(mat[r][col]))
        mat[col], mat[pivot] = mat[pivot], mat[col]
        if abs(mat[col][col]) < 1e-12:
            return None
        for row in range(col + 1, n):
            factor = mat[row][col] / mat[col][col]
            for j in range(col, n + 1):
                mat[row][j] -= factor * mat[col][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = mat[i][n]
        for j in range(i + 1, n):
            x[i] -= mat[i][j] * x[j]
        x[i] /= mat[i][i]
    return x


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------

def grid_search(
    base_config: AgentConfig,
    symbols: list[str],
    days: int,
    verbose: bool = True,
) -> AgentConfig:
    """Exhaustive grid search over key hyperparameters, ranked by Calmar ratio.

    Calmar = annualised_return / max_drawdown (higher is better risk-adjusted).
    Returns the AgentConfig that maximises Calmar on the given simulation.
    """
    grid = {
        "min_confidence_to_trade": [0.40, 0.50, 0.60],
        "trailing_stop_pct": [0.06, 0.08, 0.10, 0.12],
        "stop_loss_pct": [0.02, 0.03, 0.04],
        "max_position_pct": [0.15, 0.20, 0.25],
    }

    candidates = _expand_grid(grid)
    best_calmar = -float("inf")
    best_config = base_config
    results: list[tuple[float, dict]] = []

    for params in candidates:
        trial_config = AgentConfig(
            **{
                field: getattr(base_config, field)
                for field in base_config.__dataclass_fields__
                if field not in params and field != "db_path"
            },
            db_path=_tmp_db_path(),
            **params,
        )
        report = _quick_simulate(trial_config, symbols, days)
        calmar = report.calmar_ratio
        results.append((calmar, params))
        if calmar > best_calmar:
            best_calmar = calmar
            best_config = trial_config

    if verbose:
        results.sort(key=lambda x: -x[0])
        print(f"\nGrid Search — {len(candidates)} trials, ranked by Calmar ratio")
        print(f"{'Calmar':>7}  {'Conf':>5}  {'TStop':>6}  {'SLoss':>6}  {'MaxPos':>6}")
        print("-" * 45)
        for calmar, params in results[:10]:
            print(
                f"{calmar:>7.3f}  {params['min_confidence_to_trade']:>5.2f}  "
                f"{params['trailing_stop_pct']:>6.3f}  {params['stop_loss_pct']:>6.3f}  "
                f"{params['max_position_pct']:>6.3f}"
            )
        print(f"\nBest Calmar: {best_calmar:.3f}")

    # return config without temp db path — caller sets their own path
    return AgentConfig(
        **{
            field: getattr(best_config, field)
            for field in best_config.__dataclass_fields__
            if field != "db_path"
        },
        db_path=base_config.db_path,
    )


def _expand_grid(grid: dict[str, list]) -> list[dict]:
    keys = list(grid.keys())
    results: list[dict] = [{}]
    for key in keys:
        expanded: list[dict] = []
        for combo in results:
            for value in grid[key]:
                expanded.append({**combo, key: value})
        results = expanded
    return results


def _quick_simulate(config: AgentConfig, symbols: list[str], days: int) -> EffectivenessReport:
    try:
        runner = PaperTradingRunner(config)
        runner.simulate(symbols, days)
        report = runner.report()
        runner.store.connection.close()
        return report
    except Exception:
        return EffectivenessReport(
            starting_cash=config.starting_cash,
            ending_value=config.starting_cash,
            total_return=0.0,
            total_return_pct=0.0,
            max_drawdown_pct=0.0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            trades=0,
            calmar_ratio=-999.0,
        )
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            p = Path(f"{config.db_path}{suffix}")
            if p.exists():
                p.unlink(missing_ok=True)


def _tmp_db_path() -> str:
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        return f.name
