"""
SuperTrend indicator — rule-based, no machine learning.

Implementation follows the standard SuperTrend definition:

    ATR_n          = rolling mean of True Range over n bars
    hl2            = (high + low) / 2
    basic_upper    = hl2 + multiplier * ATR
    basic_lower    = hl2 - multiplier * ATR
    final_upper    = min(basic_upper, prev_final_upper) if close_{t-1} <= prev_final_upper
                     else basic_upper
    final_lower    = max(basic_lower, prev_final_lower) if close_{t-1} >= prev_final_lower
                     else basic_lower

    direction_t    = +1 if close_t > final_upper_{t-1}
                     -1 if close_t < final_lower_{t-1}
                     else direction_{t-1}

    supertrend_t   = final_lower_t if direction_t == +1 else final_upper_t

A long_entry is emitted on a bearish -> bullish flip, a short_entry on the
opposite flip. Signals are recorded on the candle that flips; downstream
backtesters enter at that candle's close (no look-ahead beyond OHLC close).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = ("open", "high", "low", "close")


@dataclass
class SupertrendParams:
    atr_length: int = 10
    multiplier: float = 3.0


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------


def calculate_true_range(df: pd.DataFrame) -> pd.Series:
    """True Range = max(high-low, |high-prev_close|, |low-prev_close|)."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = df["close"].astype(float).shift(1)
    range_hl = high - low
    range_hc = (high - prev_close).abs()
    range_lc = (low - prev_close).abs()
    tr = pd.concat([range_hl, range_hc, range_lc], axis=1).max(axis=1)
    # On the very first bar prev_close is NaN; fall back to simple range.
    tr.iloc[0] = float(high.iloc[0] - low.iloc[0]) if len(df) else np.nan
    return tr


def calculate_atr(df: pd.DataFrame, length: int) -> pd.Series:
    """Simple-moving-average ATR over ``length`` bars."""
    if length < 1:
        raise ValueError("ATR length must be >= 1")
    tr = calculate_true_range(df)
    # min_periods=length so callers can drop the warm-up window cleanly.
    return tr.rolling(window=length, min_periods=length).mean()


# ---------------------------------------------------------------------------
# SuperTrend
# ---------------------------------------------------------------------------


def calculate_supertrend(df: pd.DataFrame, atr_length: int, multiplier: float) -> pd.DataFrame:
    """Return a frame with supertrend_value, final_upper, final_lower, direction."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"DataFrame is missing required columns: {missing}")
    if atr_length < 1:
        raise ValueError("atr_length must be >= 1")
    if multiplier <= 0:
        raise ValueError("multiplier must be > 0")

    n = len(df)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    atr = calculate_atr(df, atr_length).to_numpy(dtype=float)
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)
    supertrend = np.full(n, np.nan)

    warmup = atr_length
    if n <= warmup:
        return pd.DataFrame(
            {
                "supertrend_value": supertrend,
                "final_upper": final_upper,
                "final_lower": final_lower,
                "direction": direction,
            },
            index=df.index,
        )

    # Seed at first valid ATR row.
    final_upper[warmup] = basic_upper[warmup]
    final_lower[warmup] = basic_lower[warmup]
    direction[warmup] = 1 if close[warmup] > basic_upper[warmup] else -1
    supertrend[warmup] = final_lower[warmup] if direction[warmup] == 1 else final_upper[warmup]

    for i in range(warmup + 1, n):
        bu = basic_upper[i]
        bl = basic_lower[i]
        prev_fu = final_upper[i - 1]
        prev_fl = final_lower[i - 1]
        prev_close = close[i - 1]

        # Final upper band: tightens unless the previous close broke it.
        if prev_close <= prev_fu:
            final_upper[i] = min(bu, prev_fu)
        else:
            final_upper[i] = bu

        # Final lower band: tightens unless the previous close broke it.
        if prev_close >= prev_fl:
            final_lower[i] = max(bl, prev_fl)
        else:
            final_lower[i] = bl

        # Direction flips on close cross of the *previous* final band.
        prev_dir = direction[i - 1]
        if prev_dir == 1:
            direction[i] = -1 if close[i] < final_lower[i] else 1
        else:
            direction[i] = 1 if close[i] > final_upper[i] else -1

        supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return pd.DataFrame(
        {
            "supertrend_value": supertrend,
            "final_upper": final_upper,
            "final_lower": final_lower,
            "direction": direction,
        },
        index=df.index,
    )


# ---------------------------------------------------------------------------
# Signals + diagnostics
# ---------------------------------------------------------------------------


def add_supertrend_signals(
    df: pd.DataFrame,
    atr_length: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """Augment ``df`` with SuperTrend value, direction, signals and diagnostics.

    Returned columns (in addition to the inputs):

    * ``supertrend_value``      — current SuperTrend line value (NaN during warm-up)
    * ``supertrend_direction``  — +1 bullish, -1 bearish, 0 during warm-up
    * ``long_entry``            — True on a bearish -> bullish flip
    * ``short_entry``           — True on a bullish -> bearish flip
    * ``supertrend_distance_pct`` — (close - supertrend) / close * 100
    * ``bars_since_flip``       — bars elapsed since the last direction flip
    """
    out = df.copy()
    st = calculate_supertrend(out, atr_length=atr_length, multiplier=multiplier)
    out["supertrend_value"] = st["supertrend_value"]
    out["supertrend_direction"] = st["direction"].astype(np.int8)

    direction = st["direction"].to_numpy(dtype=np.int8)
    prev_direction = np.concatenate([[0], direction[:-1]])
    long_entry = (direction == 1) & (prev_direction == -1)
    short_entry = (direction == -1) & (prev_direction == 1)
    out["long_entry"] = long_entry
    out["short_entry"] = short_entry

    close = out["close"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        distance_pct = np.where(
            close > 0,
            (close - st["supertrend_value"].to_numpy(dtype=float)) / close * 100.0,
            np.nan,
        )
    out["supertrend_distance_pct"] = distance_pct

    # bars_since_flip: 0 on the flip bar itself, increments every non-flip bar.
    bars_since = np.zeros(len(out), dtype=np.int32)
    counter = 0
    last_dir = 0
    for i, d in enumerate(direction):
        if d == 0:
            counter = 0
            last_dir = 0
            bars_since[i] = 0
            continue
        if d != last_dir and last_dir != 0:
            counter = 0
        else:
            counter += 1 if last_dir != 0 else 0
        bars_since[i] = counter
        last_dir = d
    out["bars_since_flip"] = bars_since

    return out


# ---------------------------------------------------------------------------
# Sanity checks (used by backtester and CLI script)
# ---------------------------------------------------------------------------


def sanity_check(df: pd.DataFrame, atr_length: int) -> list[str]:
    """Return a list of issues found in a SuperTrend-augmented frame."""
    issues: list[str] = []

    if "supertrend_value" not in df.columns or "supertrend_direction" not in df.columns:
        issues.append("missing supertrend columns")
        return issues

    warmup = atr_length
    tail = df.iloc[warmup + 1 :]
    if tail["supertrend_value"].isna().any():
        issues.append("NaN supertrend_value after warm-up")
    if not tail["supertrend_direction"].isin((-1, 1)).all():
        issues.append("supertrend_direction not in {-1, +1} after warm-up")

    # Long and short entries are mutually exclusive on each bar.
    both = (df["long_entry"] & df["short_entry"]).any()
    if both:
        issues.append("long_entry and short_entry collide on the same bar")

    return issues


__all__ = [
    "SupertrendParams",
    "calculate_true_range",
    "calculate_atr",
    "calculate_supertrend",
    "add_supertrend_signals",
    "sanity_check",
]
