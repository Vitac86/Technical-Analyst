"""Exit research: Maximum Favorable / Adverse Excursion (MFE / MAE) analysis.

Before adding more indicators or any ML/UI, this module answers a single,
practical question for the v1 strategies:

    *After a signal fires, how far does price actually travel in our favour
    (MFE) and against us (MAE) before a given holding horizon?*

The answer tells us realistic ranges for stop-loss, take-profit, trailing-stop
and holding-period settings, expressed in ATR units so the numbers are
comparable across instruments, timeframes and volatility regimes.

Design / correctness notes
--------------------------
* A signal on bar ``i`` is entered at the **open of bar ``i + 1``** (same
  no-lookahead rule the backtester uses). The signal bar's own high/low are
  therefore *excluded* from the excursion window.
* ``atr_at_entry`` is the ATR of the **signal bar** (the last fully completed
  bar before entry). Using a completed-bar ATR keeps the normalisation free of
  lookahead. Signals whose ATR is missing/non-positive are skipped.
* The excursion window is the first ``max_holding_bars`` bars starting at the
  entry bar (inclusive). To keep horizon/excursion statistics free of
  truncation bias, a signal is skipped when a full window of future bars is not
  available near the end of the dataset.
* "R" is defined as **1 ATR** of movement. ``reached_*r`` flags measure how far
  the favorable excursion ran; ``touched_minus_*r`` flags measure how far the
  adverse excursion ran. This is the natural unit when the goal is to *discover*
  a stop rather than assume one.

The module depends on pandas/numpy only and never mutates its inputs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

try:  # package import (e.g. ``from app.strategy_lab import exit_research``)
    from . import indicators
except ImportError:  # script import (``python .../run_exit_research.py``)
    import indicators  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# R-levels. R == 1 ATR of price movement.
#   * favorable levels -> ``reached_*r`` flags (how far MFE ran)
#   * adverse  levels  -> ``touched_minus_*r`` flags (how far MAE ran)
# Each pair is (threshold_in_atr, output_column_name) so the column names are
# explicit and never depend on float formatting.
# ---------------------------------------------------------------------------
REACHED_LEVELS: tuple[tuple[float, str], ...] = (
    (2.0, "reached_2r"),
    (3.0, "reached_3r"),
    (5.0, "reached_5r"),
    (8.0, "reached_8r"),
    (12.0, "reached_12r"),
)

TOUCHED_LEVELS: tuple[tuple[float, str], ...] = (
    (1.0, "touched_minus_1r"),
    (1.5, "touched_minus_1_5r"),
    (2.0, "touched_minus_2r"),
    (2.5, "touched_minus_2_5r"),
    (3.0, "touched_minus_3r"),
)

# Column order for the per-signal record table.
SIGNAL_RECORD_COLUMNS: tuple[str, ...] = (
    "strategy_name",
    "symbol",
    "timeframe",
    "direction",
    "signal_time",
    "entry_time",
    "entry_price",
    "atr_at_entry",
    "max_holding_bars",
    "max_favorable_excursion",
    "max_adverse_excursion",
    "max_favorable_excursion_atr",
    "max_adverse_excursion_atr",
    "bars_to_mfe",
    "bars_to_mae",
    "close_pnl_at_horizon",
    "close_pnl_at_horizon_atr",
    *(col for _, col in REACHED_LEVELS),
    *(col for _, col in TOUCHED_LEVELS),
)

# Column order for the aggregated summary table.
SUMMARY_COLUMNS: tuple[str, ...] = (
    "strategy_name",
    "timeframe",
    "direction",
    "max_holding_bars",
    "total_signals",
    "median_mfe_atr",
    "p75_mfe_atr",
    "p90_mfe_atr",
    "p95_mfe_atr",
    "median_mae_atr",
    "p75_mae_atr",
    "p90_mae_atr",
    "median_bars_to_mfe",
    "p75_bars_to_mfe",
    "average_close_pnl_at_horizon_atr",
    "median_close_pnl_at_horizon_atr",
    *(f"{col}_rate" for _, col in REACHED_LEVELS),
    *(f"{col}_rate" for _, col in TOUCHED_LEVELS),
)

# Column order for the recommended exit-ranges table. Carries the handful of
# summary fields needed to rank/sort the recommendations downstream.
RECOMMENDATION_COLUMNS: tuple[str, ...] = (
    "strategy_name",
    "timeframe",
    "direction",
    "max_holding_bars",
    "total_signals",
    "suggested_stop_loss_atr",
    "suggested_take_profit_atr",
    "trend_potential_score",
    "p75_mae_atr",
    "p90_mfe_atr",
    "reached_5r_rate",
    "reached_8r_rate",
    "average_close_pnl_at_horizon_atr",
    "median_bars_to_mfe",
    "notes",
)


# ---------------------------------------------------------------------------
# Per-signal MFE / MAE analysis
# ---------------------------------------------------------------------------

def analyze_signals(
    df: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    strategy_name: str,
    symbol: str | None = None,
    timeframe: str | None = None,
    atr_period: int = 14,
    max_holding_bars: int = 48,
) -> pd.DataFrame:
    """Compute an MFE/MAE record for every signal in ``signals``.

    ``df`` and ``signals`` must be row-aligned (same order/length), as produced
    by :mod:`app.strategy_lab.data_loader` and :mod:`app.strategy_lab.strategies`.

    Returns a DataFrame with one row per analysable signal and the columns in
    :data:`SIGNAL_RECORD_COLUMNS`. Signals are skipped (not analysed) when:
      * they fall on the last bar (no next-bar open to enter on),
      * their entry ATR is missing or non-positive, or
      * a full ``max_holding_bars`` future window is unavailable (end of data).
    """
    if len(df) != len(signals):
        raise ValueError("df and signals must have the same number of rows")
    if max_holding_bars < 1:
        raise ValueError("max_holding_bars must be >= 1")

    if symbol is None and "symbol" in df.columns and len(df):
        symbol = df["symbol"].iloc[0]
    if timeframe is None and "timeframe" in df.columns and len(df):
        timeframe = df["timeframe"].iloc[0]

    open_ = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    times = df["datetime"].to_numpy()

    # ATR of every bar; the entry uses the *signal* bar's value (no lookahead).
    atr_values = indicators.atr(df, atr_period).to_numpy(dtype=float)
    signal = signals["signal"].to_numpy(dtype=int)

    n = len(df)
    records: list[dict] = []

    # Only iterate the bars that actually carry a signal.
    for i in np.flatnonzero(signal != 0):
        i = int(i)
        entry_idx = i + 1
        if entry_idx >= n:
            continue  # signal on the last bar -> cannot enter on a next open

        atr_at_entry = atr_values[i]
        if np.isnan(atr_at_entry) or atr_at_entry <= 0.0:
            continue  # cannot normalise -> skip the signal

        # Future window: [entry_idx, entry_idx + max_holding_bars) (exclusive).
        # Require a full window so excursion/horizon stats are not truncated.
        window_end = entry_idx + max_holding_bars
        if window_end > n:
            continue

        record = _evaluate_trade(
            direction=int(signal[i]),
            entry_idx=entry_idx,
            window_end=window_end,
            entry_price=float(open_[entry_idx]),
            atr_at_entry=float(atr_at_entry),
            high=high,
            low=low,
            close=close,
            times=times,
        )
        record.update(
            {
                "strategy_name": strategy_name,
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": "long" if signal[i] == 1 else "short",
                "signal_time": times[i],
                "max_holding_bars": max_holding_bars,
            }
        )
        records.append(record)

    return pd.DataFrame(records, columns=list(SIGNAL_RECORD_COLUMNS))


def _evaluate_trade(
    *,
    direction: int,
    entry_idx: int,
    window_end: int,
    entry_price: float,
    atr_at_entry: float,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    times: np.ndarray,
) -> dict:
    """Build the excursion record for a single hypothetical trade.

    ``bars_to_mfe`` / ``bars_to_mae`` are 1-indexed: a value of 1 means the
    extreme occurred on the entry bar itself (consistent with the backtester's
    ``bars_held`` convention). ``np.argmax``/``np.argmin`` return the *first*
    occurrence, i.e. the earliest bar at which the extreme is reached.
    """
    fut_high = high[entry_idx:window_end]
    fut_low = low[entry_idx:window_end]
    horizon_close = float(close[window_end - 1])

    if direction == 1:  # long
        mfe = float(fut_high.max() - entry_price)
        mae = float(entry_price - fut_low.min())
        bars_to_mfe = int(np.argmax(fut_high)) + 1
        bars_to_mae = int(np.argmin(fut_low)) + 1
        close_pnl = horizon_close - entry_price
    else:  # short
        mfe = float(entry_price - fut_low.min())
        mae = float(fut_high.max() - entry_price)
        bars_to_mfe = int(np.argmin(fut_low)) + 1
        bars_to_mae = int(np.argmax(fut_high)) + 1
        close_pnl = entry_price - horizon_close

    # Excursions are non-negative by construction (the entry bar's high >= open
    # and low <= open), so MFE/MAE are always >= 0.
    mfe_atr = mfe / atr_at_entry
    mae_atr = mae / atr_at_entry

    record: dict = {
        "entry_time": times[entry_idx],
        "entry_price": entry_price,
        "atr_at_entry": atr_at_entry,
        "max_favorable_excursion": mfe,
        "max_adverse_excursion": mae,
        "max_favorable_excursion_atr": mfe_atr,
        "max_adverse_excursion_atr": mae_atr,
        "bars_to_mfe": bars_to_mfe,
        "bars_to_mae": bars_to_mae,
        "close_pnl_at_horizon": float(close_pnl),
        "close_pnl_at_horizon_atr": float(close_pnl) / atr_at_entry,
    }
    # R-level flags (R == 1 ATR).
    for level, col in REACHED_LEVELS:
        record[col] = bool(mfe_atr >= level)
    for level, col in TOUCHED_LEVELS:
        record[col] = bool(mae_atr >= level)
    return record


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------

_GROUP_COLUMNS: tuple[str, ...] = (
    "strategy_name",
    "timeframe",
    "direction",
    "max_holding_bars",
)


def _pct(series: pd.Series, q: float) -> float:
    """Percentile ``q`` (0-100) of a series, as a plain float."""
    return float(np.percentile(series.to_numpy(dtype=float), q))


def summarize(signals: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-signal records into grouped excursion statistics.

    Grouped by ``strategy_name``, ``timeframe``, ``direction`` and
    ``max_holding_bars``; returns the columns in :data:`SUMMARY_COLUMNS`.
    """
    if signals is None or signals.empty:
        return pd.DataFrame(columns=list(SUMMARY_COLUMNS))

    group_cols = list(_GROUP_COLUMNS)
    rows: list[dict] = []
    for keys, group in signals.groupby(group_cols, sort=True):
        rows.append(_summarize_group(dict(zip(group_cols, keys)), group))
    return pd.DataFrame(rows, columns=list(SUMMARY_COLUMNS))


def _summarize_group(keys: dict, group: pd.DataFrame) -> dict:
    """Excursion statistics for one (strategy, tf, direction, holding) group."""
    mfe_atr = group["max_favorable_excursion_atr"]
    mae_atr = group["max_adverse_excursion_atr"]
    bars = group["bars_to_mfe"]
    close_atr = group["close_pnl_at_horizon_atr"]

    row: dict = {
        **keys,
        "total_signals": int(len(group)),
        "median_mfe_atr": float(mfe_atr.median()),
        "p75_mfe_atr": _pct(mfe_atr, 75),
        "p90_mfe_atr": _pct(mfe_atr, 90),
        "p95_mfe_atr": _pct(mfe_atr, 95),
        "median_mae_atr": float(mae_atr.median()),
        "p75_mae_atr": _pct(mae_atr, 75),
        "p90_mae_atr": _pct(mae_atr, 90),
        "median_bars_to_mfe": float(bars.median()),
        "p75_bars_to_mfe": _pct(bars, 75),
        "average_close_pnl_at_horizon_atr": float(close_atr.mean()),
        "median_close_pnl_at_horizon_atr": float(close_atr.median()),
    }
    # Hit-rate of each R-level flag is just the mean of the boolean column.
    for _, col in REACHED_LEVELS:
        row[f"{col}_rate"] = float(group[col].mean())
    for _, col in TOUCHED_LEVELS:
        row[f"{col}_rate"] = float(group[col].mean())
    return row


# ---------------------------------------------------------------------------
# Recommended exit ranges
# ---------------------------------------------------------------------------

def _round_to_half(value: float, *, lo: float, hi: float) -> float:
    """Round to the nearest 0.5 ATR, then clamp into ``[lo, hi]``."""
    if value is None or np.isnan(value):
        return lo
    rounded = round(value * 2.0) / 2.0
    return float(min(max(rounded, lo), hi))


def _trend_potential_score(row: pd.Series) -> float:
    """A simple, readable trend-potential score (higher == more runner-like).

    Rewards: a large p90 favorable excursion, a high rate of large favorable
    moves (5R / 8R), and positive average PnL when holding to the horizon.
    Penalises: a wide p75 adverse excursion (trades that need a wide stop).
    Weights are deliberately hand-picked and easy to tweak.
    """
    return round(
        float(row["p90_mfe_atr"])
        + 4.0 * float(row["reached_5r_rate"])
        + 6.0 * float(row["reached_8r_rate"])
        + 2.0 * float(row["average_close_pnl_at_horizon_atr"])
        - 1.0 * float(row["p75_mae_atr"]),
        3,
    )


def _make_notes(row: dict) -> str:
    """Short human-readable guidance based on the group's excursion profile.

    Thresholds are heuristic. Several notes may apply and are joined by "; ".
    """
    notes: list[str] = []

    # Trend strength: frequent very-large favorable moves or a high overall score.
    if row["reached_8r_rate"] >= 0.08 or row["trend_potential_score"] >= 8.0:
        notes.append("strong trend potential")

    # Risk profile: typical adverse excursion is large -> needs room.
    if row["p75_mae_atr"] >= 2.5:
        notes.append("wide stop needed")

    # Upside: limited favorable travel or few large moves.
    if row["p90_mfe_atr"] < 3.0 or row["reached_5r_rate"] < 0.05:
        notes.append("weak upside")

    # Holding preference: how early (relative to the horizon) the MFE lands.
    holding = row["max_holding_bars"]
    frac = row["median_bars_to_mfe"] / holding if holding else 0.0
    if frac <= 0.33:
        notes.append("short holding preferred")
    elif frac >= 0.66:
        notes.append("long holding preferred")

    if not notes:
        notes.append("neutral profile")
    return "; ".join(notes)


def recommend_exit_ranges(summary: pd.DataFrame) -> pd.DataFrame:
    """Turn the summary into suggested stop/take-profit ranges and notes.

    For each summary row:
      * ``suggested_stop_loss_atr``   - conservative, from p75 MAE, nearest 0.5,
        capped to ``[1.0, 5.0]``.
      * ``suggested_take_profit_atr`` - from p90 MFE (the upper-realistic trend
        move), nearest 0.5, capped to ``[2.0, 20.0]``.
      * ``trend_potential_score``     - see :func:`_trend_potential_score`.
      * ``notes``                     - short text guidance.
    """
    if summary is None or summary.empty:
        return pd.DataFrame(columns=list(RECOMMENDATION_COLUMNS))

    rows: list[dict] = []
    for _, s in summary.iterrows():
        row: dict = {
            "strategy_name": s["strategy_name"],
            "timeframe": s["timeframe"],
            "direction": s["direction"],
            "max_holding_bars": int(s["max_holding_bars"]),
            "total_signals": int(s["total_signals"]),
            # Stop from the typical (p75) adverse move; take-profit from the
            # upper-realistic (p90) favorable move.
            "suggested_stop_loss_atr": _round_to_half(s["p75_mae_atr"], lo=1.0, hi=5.0),
            "suggested_take_profit_atr": _round_to_half(s["p90_mfe_atr"], lo=2.0, hi=20.0),
            "trend_potential_score": _trend_potential_score(s),
            "p75_mae_atr": float(s["p75_mae_atr"]),
            "p90_mfe_atr": float(s["p90_mfe_atr"]),
            "reached_5r_rate": float(s["reached_5r_rate"]),
            "reached_8r_rate": float(s["reached_8r_rate"]),
            "average_close_pnl_at_horizon_atr": float(s["average_close_pnl_at_horizon_atr"]),
            "median_bars_to_mfe": float(s["median_bars_to_mfe"]),
        }
        row["notes"] = _make_notes(row)
        rows.append(row)

    return pd.DataFrame(rows, columns=list(RECOMMENDATION_COLUMNS))
