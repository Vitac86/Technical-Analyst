"""Focused MFE/MAE exit-research runner for the Strategy Lab.

Runs a *targeted* (not brute-force) excursion study for the strongest v1
candidates on XAUUSD H1 and H4, then writes three CSVs and prints leaderboards
that show where the real trend potential and risk live.

Run it directly::

    python backend/app/strategy_lab/run_exit_research.py

or as a module from the ``backend`` directory::

    python -m app.strategy_lab.run_exit_research

Outputs (under ``MetaTrader_Data/reports/exit_research/``):
    * ``mfe_mae_signals.csv``         - one row per analysed signal.
    * ``mfe_mae_summary.csv``         - excursion stats per group.
    * ``recommended_exit_ranges.csv`` - suggested SL/TP + trend score + notes.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

# Allow execution both as a script and as a package module.
try:
    from . import data_loader, exit_research, strategies
except ImportError:  # running as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import data_loader  # type: ignore[no-redef]
    import exit_research  # type: ignore[no-redef]
    import strategies  # type: ignore[no-redef]

# Repo root: run_exit_research.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "MetaTrader_Data" / "mt5_exports"
OUTPUT_DIR = REPO_ROOT / "MetaTrader_Data" / "reports" / "exit_research"

# ATR period used to normalise excursions (independent of any strategy's own
# internal ATR). 14 matches the backtester default.
ATR_PERIOD = 14

# Source file per timeframe (read-only; never modified).
TIMEFRAME_FILES: dict[str, str] = {
    "H1": "XAUUSDrfd_H1.csv",
    "H4": "XAUUSDrfd_H4.csv",
}

# Holding horizons to study per timeframe (in bars).
HOLDING_PERIODS: dict[str, list[int]] = {
    "H1": [24, 48, 72, 120, 240, 480],
    "H4": [12, 24, 48, 72, 120, 240],
}


# ---------------------------------------------------------------------------
# Focused strategy / parameter selection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyRun:
    """One named strategy configuration to analyse."""

    name: str
    signal_fn: Callable[..., pd.DataFrame]
    params: dict = field(default_factory=dict)


def build_runs() -> list[StrategyRun]:
    """Return the focused set of strategy configurations to analyse.

    Parameter variants are encoded into ``name`` so that each variant forms its
    own summary group (e.g. ``ema_50_100`` vs ``ema_50_200``) instead of being
    merged under a single strategy name.
    """
    runs: list[StrategyRun] = []

    # A. EMA crossover
    for fast, slow in ((50, 100), (50, 150), (50, 200), (30, 100)):
        runs.append(
            StrategyRun(
                name=f"ema_{fast}_{slow}",
                signal_fn=strategies.ema_crossover_strategy,
                params={"fast_period": fast, "slow_period": slow},
            )
        )

    # B. Donchian breakout
    for lookback in (40, 55, 80, 100):
        runs.append(
            StrategyRun(
                name=f"donchian_{lookback}",
                signal_fn=strategies.donchian_breakout_strategy,
                params={"lookback": lookback},
            )
        )

    # C. SuperTrend
    for atr_period, multiplier in ((10, 2.0), (10, 2.5), (14, 2.0), (14, 2.5)):
        runs.append(
            StrategyRun(
                name=f"supertrend_{atr_period}_{multiplier:g}",
                signal_fn=strategies.supertrend_strategy,
                params={"atr_period": atr_period, "multiplier": multiplier},
            )
        )

    return runs


# ---------------------------------------------------------------------------
# Analysis driver
# ---------------------------------------------------------------------------

def analyze_all(
    data_dir: Path,
    *,
    atr_period: int = ATR_PERIOD,
) -> pd.DataFrame:
    """Run every (timeframe x strategy x holding) combination.

    Signals are generated once per (timeframe, strategy) and reused across all
    holding horizons. Returns the concatenated per-signal records.
    """
    frames: list[pd.DataFrame] = []
    runs = build_runs()

    for timeframe, filename in TIMEFRAME_FILES.items():
        path = data_dir / filename
        print(f"Loading {path} ...")
        df = data_loader.load_mt5_csv(path)
        symbol = df["symbol"].iloc[0] if len(df) else None
        tf = df["timeframe"].iloc[0] if len(df) else timeframe
        print(
            f"  {len(df)} bars "
            f"({df['datetime'].iloc[0]} -> {df['datetime'].iloc[-1]})"
        )

        for run in runs:
            signals = run.signal_fn(df, **run.params)
            for holding in HOLDING_PERIODS[timeframe]:
                records = exit_research.analyze_signals(
                    df,
                    signals,
                    strategy_name=run.name,
                    symbol=symbol,
                    timeframe=tf,
                    atr_period=atr_period,
                    max_holding_bars=holding,
                )
                if len(records):
                    frames.append(records)

    if not frames:
        return pd.DataFrame(columns=list(exit_research.SIGNAL_RECORD_COLUMNS))
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

# Compact column set for the printed leaderboards.
_LEADERBOARD_COLUMNS = [
    "strategy_name",
    "timeframe",
    "direction",
    "max_holding_bars",
    "total_signals",
    "suggested_stop_loss_atr",
    "suggested_take_profit_atr",
    "trend_potential_score",
    "reached_8r_rate",
    "average_close_pnl_at_horizon_atr",
    "notes",
]


def _print_leaderboard(reco: pd.DataFrame, by: str, n: int = 20) -> None:
    """Print the top ``n`` recommendation rows sorted by ``by`` (descending)."""
    if reco.empty:
        print(f"\n(no recommendations to rank by {by})")
        return
    ranked = reco.sort_values(by, ascending=False).head(n)
    print(f"\n=== Top {n} by {by} (descending) ===")
    with pd.option_context("display.max_columns", None, "display.width", 240):
        print(ranked[_LEADERBOARD_COLUMNS].to_string(index=False))


def print_report(signals: pd.DataFrame, reco: pd.DataFrame, output_dir: Path) -> None:
    """Print the end-of-run console summary requested by the task."""
    print(f"\nTotal analysed signals: {len(signals)}")
    print(f"Output folder: {output_dir}")

    _print_leaderboard(reco, "trend_potential_score")
    _print_leaderboard(reco, "reached_8r_rate")
    _print_leaderboard(reco, "average_close_pnl_at_horizon_atr")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strategy Lab exit-research runner")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR,
        help="Directory holding the MT5 CSV exports (read-only).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory to write the exit-research CSVs into.",
    )
    parser.add_argument("--atr-period", type=int, default=ATR_PERIOD)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    signals = analyze_all(args.data_dir, atr_period=args.atr_period)
    summary = exit_research.summarize(signals)
    reco = exit_research.recommend_exit_ranges(summary)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    signals_path = args.output_dir / "mfe_mae_signals.csv"
    summary_path = args.output_dir / "mfe_mae_summary.csv"
    reco_path = args.output_dir / "recommended_exit_ranges.csv"
    signals.to_csv(signals_path, index=False)
    summary.to_csv(summary_path, index=False)
    reco.to_csv(reco_path, index=False)

    print(f"\nWrote {len(signals)} signal rows   -> {signals_path}")
    print(f"Wrote {len(summary)} summary rows  -> {summary_path}")
    print(f"Wrote {len(reco)} recommendations -> {reco_path}")

    print_report(signals, reco, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
