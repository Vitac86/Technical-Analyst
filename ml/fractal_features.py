"""
Confirmed fractal detection and derived features.

LEAKAGE PREVENTION
------------------
A fractal high at bar i requires right_span future candles to confirm.
It is therefore only CONFIRMED at bar i + right_span — the earliest bar
at which a model or strategy may use this information.

All usable columns (last_fractal_*, bars_since_*, distance_*) forward-fill
from confirmation bars only. No future data is embedded in these columns.

The raw fractal_high_price / fractal_low_price columns are provided for
diagnostics only — they carry leakage relative to their own bar and must
NOT be used as model features.

Usage:
    from fractal_features import add_confirmed_fractals_grouped, FRACTAL_FEATURE_COLUMNS
    df = add_confirmed_fractals_grouped(df)
"""
import numpy as np
import pandas as pd

FRACTAL_FEATURE_COLUMNS = [
    "bars_since_fractal_high",
    "bars_since_fractal_low",
    "distance_to_last_fractal_high_pct",
    "distance_to_last_fractal_low_pct",
]


def detect_fractal_high(
    df: pd.DataFrame,
    left_span: int = 2,
    right_span: int = 2,
) -> pd.Series:
    """
    Boolean Series: True where bar i is a raw (unconfirmed) fractal high.

      high[i] > high[i-left_span .. i-1]   AND
      high[i] > high[i+1 .. i+right_span]

    WARNING: uses future bars for the right-side condition.
    Do NOT pass this directly to a model. Use add_confirmed_fractals().
    """
    h = df["high"]
    # Left: h[i] exceeds the rolling max of the previous left_span bars.
    left_max = h.shift(1).rolling(left_span, min_periods=left_span).max()
    left_ok = h > left_max

    # Right: h[i] exceeds max(h[i+1 .. i+right_span]).
    # h.rolling(right_span).max().shift(-right_span) at position i
    # evaluates to max(h[i+1 .. i+right_span]).
    right_max = h.rolling(right_span).max().shift(-right_span)
    right_ok = h > right_max

    return (left_ok & right_ok).fillna(False)


def detect_fractal_low(
    df: pd.DataFrame,
    left_span: int = 2,
    right_span: int = 2,
) -> pd.Series:
    """
    Boolean Series: True where bar i is a raw (unconfirmed) fractal low.

      low[i] < low[i-left_span .. i-1]   AND
      low[i] < low[i+1 .. i+right_span]

    WARNING: uses future bars — do not use as a model feature.
    """
    l = df["low"]
    left_min = l.shift(1).rolling(left_span, min_periods=left_span).min()
    left_ok = l < left_min

    right_min = l.rolling(right_span).min().shift(-right_span)
    right_ok = l < right_min

    return (left_ok & right_ok).fillna(False)


def add_confirmed_fractals(
    df: pd.DataFrame,
    left_span: int = 2,
    right_span: int = 2,
) -> pd.DataFrame:
    """
    Add confirmed-fractal columns to df.

    Call per-ticker with a reset integer index; do not mix tickers.

    Columns added
    -------------
    fractal_high_price            diagnostic; leakage at its own bar
    fractal_low_price             diagnostic; leakage at its own bar
    confirmed_fractal_high_price  price placed at confirmation bar (i+right_span)
    confirmed_fractal_low_price
    last_fractal_high             forward-filled confirmed high — safe as feature
    last_fractal_low              forward-filled confirmed low  — safe as feature
    bars_since_fractal_high       bars since last confirmation event
    bars_since_fractal_low
    distance_to_last_fractal_high_pct  (last_high - close) / close * 100
    distance_to_last_fractal_low_pct   (last_low  - close) / close * 100
    """
    df = df.copy().reset_index(drop=True)
    n = len(df)
    h = df["high"]
    l = df["low"]
    c = df["close"]

    # --- Raw fractal detection (may use future bars) ---
    is_frac_high = detect_fractal_high(df, left_span, right_span)
    is_frac_low  = detect_fractal_low(df,  left_span, right_span)

    df["fractal_high_price"] = h.where(is_frac_high)
    df["fractal_low_price"]  = l.where(is_frac_low)

    # --- Confirmed fractals: shift raw signal forward by right_span ---
    # At bar j = i + right_span, the fractal at bar i is confirmed.
    # confirmed_fractal_high_price[j] = h[j - right_span]
    conf_high_flag = is_frac_high.shift(right_span).fillna(False).astype(bool)
    conf_low_flag  = is_frac_low.shift(right_span).fillna(False).astype(bool)

    confirmed_high_price = h.shift(right_span).where(conf_high_flag)
    confirmed_low_price  = l.shift(right_span).where(conf_low_flag)

    df["confirmed_fractal_high_price"] = confirmed_high_price
    df["confirmed_fractal_low_price"]  = confirmed_low_price

    # --- Forward-fill: last known confirmed fractal at every bar ---
    df["last_fractal_high"] = confirmed_high_price.ffill()
    df["last_fractal_low"]  = confirmed_low_price.ffill()

    # --- Bars since the most recent confirmation event ---
    bar_idx = pd.Series(np.arange(n, dtype=float), index=df.index)
    last_conf_high_bar = bar_idx.where(conf_high_flag).ffill()
    last_conf_low_bar  = bar_idx.where(conf_low_flag).ffill()

    df["bars_since_fractal_high"] = (bar_idx - last_conf_high_bar).where(
        last_conf_high_bar.notna()
    )
    df["bars_since_fractal_low"] = (bar_idx - last_conf_low_bar).where(
        last_conf_low_bar.notna()
    )

    # --- Distance from close to last confirmed fractal (%) ---
    df["distance_to_last_fractal_high_pct"] = (
        (df["last_fractal_high"] - c) / c * 100
    ).where(c > 0)
    df["distance_to_last_fractal_low_pct"] = (
        (df["last_fractal_low"] - c) / c * 100
    ).where(c > 0)

    return df


def add_confirmed_fractals_grouped(
    df: pd.DataFrame,
    ticker_col: str = "ticker",
    left_span: int = 2,
    right_span: int = 2,
) -> pd.DataFrame:
    """
    Apply add_confirmed_fractals per ticker and re-assemble.

    Preserves the original row order via a temporary integer sort key.
    """
    df = df.copy()
    df["_frac_sort"] = np.arange(len(df))

    frames = []
    for _, grp in df.groupby(ticker_col, sort=False):
        grp = grp.sort_values("datetime").reset_index(drop=True)
        grp = add_confirmed_fractals(grp, left_span=left_span, right_span=right_span)
        frames.append(grp)

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values("_frac_sort").reset_index(drop=True)
    return result.drop(columns=["_frac_sort"])
