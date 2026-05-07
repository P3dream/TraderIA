from __future__ import annotations

import tempfile
from pathlib import Path

from traderia.config import AgentConfig
from traderia.models import EffectivenessReport, WalkForwardFold
from traderia.runner import PaperTradingRunner
from traderia.storage import SQLiteStore


def walk_forward_validate(
    config: AgentConfig,
    symbols: list[str],
    total_days: int = 500,
    train_window: int = 252,
    test_window: int = 63,
    step: int = 21,
) -> list[WalkForwardFold]:
    """Out-of-sample validation via rolling walk-forward folds.

    For each fold the agent is initialized fresh (no cross-fold learning leak)
    and run on `test_window` days of data that were NOT used to choose parameters.
    The caller fits / tunes parameters on `train_window` data before calling this.

    Returns one WalkForwardFold per fold, ordered chronologically.
    """
    folds: list[WalkForwardFold] = []
    fold_index = 0
    start = train_window  # first test fold begins after the first training window

    while start + test_window <= total_days:
        fold_report = _run_fold(config, symbols, days=test_window, fold_index=fold_index)
        folds.append(WalkForwardFold(
            fold=fold_index,
            train_days=train_window,
            test_days=test_window,
            report=fold_report,
        ))
        start += step
        fold_index += 1

    return folds


def _run_fold(config: AgentConfig, symbols: list[str], days: int, fold_index: int) -> EffectivenessReport:
    with tempfile.NamedTemporaryFile(suffix=f"_fold{fold_index}.sqlite3", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        fold_config = AgentConfig(
            **{
                field: getattr(config, field)
                for field in config.__dataclass_fields__
                if field != "db_path"
            },
            db_path=tmp_path,
        )
        runner = PaperTradingRunner(fold_config)
        runner.simulate(symbols, days)
        report = runner.report()
        runner.store.connection.close()
        return report
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            p = Path(f"{tmp_path}{suffix}")
            if p.exists():
                p.unlink()


def print_walk_forward_summary(folds: list[WalkForwardFold]) -> None:
    print(f"\nWalk-Forward Validation — {len(folds)} folds")
    print(f"{'Fold':>4}  {'Return%':>8}  {'MaxDD%':>8}  {'Sharpe':>7}  {'Calmar':>7}  {'WinRate%':>9}  {'Trades':>6}")
    print("-" * 65)
    for fold in folds:
        r = fold.report
        print(
            f"{fold.fold:>4}  {r.total_return_pct:>+8.2f}  {r.max_drawdown_pct:>8.2f}  "
            f"{r.sharpe_ratio:>7.2f}  {r.calmar_ratio:>7.2f}  {r.win_rate_pct:>9.1f}  {r.trades:>6}"
        )

    returns = [f.report.total_return_pct for f in folds]
    sharpes = [f.report.sharpe_ratio for f in folds]
    calmars = [f.report.calmar_ratio for f in folds]
    wins = [f.report.win_rate_pct for f in folds]
    import statistics
    print("-" * 65)
    print(
        f"{'Avg':>4}  {statistics.fmean(returns):>+8.2f}  {'':>8}  "
        f"{statistics.fmean(sharpes):>7.2f}  {statistics.fmean(calmars):>7.2f}  {statistics.fmean(wins):>9.1f}"
    )
    positive_folds = sum(1 for r in returns if r > 0)
    print(f"\nPositive folds: {positive_folds}/{len(folds)}")
