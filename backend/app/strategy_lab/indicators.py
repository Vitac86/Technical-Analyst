"""Technical indicators implemented with pandas/numpy only.

All functions are pure: they take price data and return new Series/DataFrames
without mutating their inputs. Wilder-style smoothing (used by ATR, RSI and
ADX) is implemented as a recursive moving average (RMA) via ``ewm`` with
``alpha = 1 / period`` and ``adjust=False`` so values match common MT5/TA
conventions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's recursive moving average (a.k.a. RMA / SMMA)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    """True range: max of (H-L, |H-prevC|, |L-prevC|)."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range using Wilder smoothing."""
    tr = true_range(df)
    return _wilder_rma(tr, period)


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder smoothing."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = _wilder_rma(gain, period)
    avg_loss = _wilder_rma(loss, period)

    rs = avg_gain / avg_loss
    rsi_values = 100.0 - (100.0 / (1.0 + rs))
    # When there are no losses, RS is +inf -> RSI = 100; make that explicit.
    rsi_values = rsi_values.where(avg_loss != 0, 100.0)
    return rsi_values


def donchian_channel(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """Donchian channel.

    ``upper``/``lower`` are the rolling high/low over ``lookback`` bars and
    therefore *include* the current bar. Breakout strategies must shift these
    by one bar to avoid using a bar to generate its own breakout level.
    """
    upper = df["high"].rolling(window=lookback, min_periods=lookback).max()
    lower = df["low"].rolling(window=lookback, min_periods=lookback).min()
    middle = (upper + lower) / 2.0
    return pd.DataFrame(
        {"upper": upper, "lower": lower, "middle": middle}, index=df.index
    )


def supertrend(
    df: pd.DataFrame,
    atr_period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """SuperTrend indicator.

    Returns a DataFrame with:
        * ``supertrend`` - the trailing stop line
        * ``direction``  - +1 for an up-trend (bullish), -1 for a down-trend

    The classic recursive band logic is used; ``direction`` flips only when
    ``close`` crosses the opposite final band.
    """
    atr_values = atr(df, atr_period)
    hl2 = (df["high"] + df["low"]) / 2.0

    upper_band = (hl2 + multiplier * atr_values).to_numpy()
    lower_band = (hl2 - multiplier * atr_values).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)

    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)
    trend = np.full(n, np.nan)

    for i in range(n):
        if i == 0 or np.isnan(upper_band[i]) or np.isnan(upper_band[i - 1]):
            final_upper[i] = upper_band[i]
            final_lower[i] = lower_band[i]
            direction[i] = 1
            trend[i] = lower_band[i]
            continue

        # Tighten the bands while price stays inside them.
        if upper_band[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = upper_band[i]
        else:
            final_upper[i] = final_upper[i - 1]

        if lower_band[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = lower_band[i]
        else:
            final_lower[i] = final_lower[i - 1]

        # Determine trend direction from a close crossing the prior final band.
        if close[i] > final_upper[i - 1]:
            direction[i] = 1
        elif close[i] < final_lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

        trend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return pd.DataFrame(
        {"supertrend": trend, "direction": direction}, index=df.index
    )


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Average Directional Index with +DI/-DI (Wilder smoothing).

    Returns a DataFrame with columns ``adx``, ``plus_di`` and ``minus_di``.
    Provided for completeness; not required by the v1 strategies.
    """
    high = df["high"]
    low = df["low"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    atr_values = _wilder_rma(true_range(df), period)
    plus_di = 100.0 * _wilder_rma(plus_dm, period) / atr_values
    minus_di = 100.0 * _wilder_rma(minus_dm, period) / atr_values

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_values = _wilder_rma(dx.fillna(0.0), period)

    return pd.DataFrame(
        {"adx": adx_values, "plus_di": plus_di, "minus_di": minus_di},
        index=df.index,
    )
