"""No-leakage feature engineering for the ML signal filter (Strategy Lab v1.5).

The ML layer in v1.5 **only filters already-existing rule-based long signals**.
It never predicts price and never generates trades. To stay honest about that,
every feature here is computed using *only information that is known at the
signal candle* (the last completed bar before entry):

    * Rolling indicators are built from historical candles up to and including
      the signal bar. ``pandas`` rolling / ewm windows look *backwards* only, so
      a value at row ``i`` never uses rows ``> i``.
    * The backtester enters on the **next** bar's open. We therefore attach
      features taken at the *signal* bar (``signal_idx``), never at the entry
      bar. Entry-candle OHLC is never read as a feature input.
    * Trade outcomes (exit_time, exit_reason, net_pnl, r_multiple, MFE, MAE,
      bars_held, ...) are *never* used as feature inputs. They are joined on
      afterwards as targets / diagnostics only.
    * Higher-timeframe context is merged with an ``asof`` *backward* merge using
      a conservative cutoff (``signal_time - higher_timeframe_duration``) so an
      incomplete higher-timeframe candle can never leak into a lower-timeframe
      row (see :func:`merge_htf_features`).

The module depends on pandas/numpy only and never mutates its inputs.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

try:  # package import
    from . import indicators
except ImportError:  # script import
    import indicators  # type: ignore[no-redef]


# Reference SuperTrend used purely as a *regime feature* (independent of any
# trade configuration). A fixed reference keeps the feature comparable across
# every finalist/variant and avoids leaking the trade's own parameters.
_ST_REF_ATR_PERIOD = 10
_ST_REF_MULTIPLIER = 3.0

# Substrings that must never appear in a *feature* column name. They flag a
# column that is an outcome / target / future value rather than a known-at-
# signal-time input. Checked by :func:`assert_feature_names_clean`.
FORBIDDEN_FEATURE_SUBSTRINGS: tuple[str, ...] = (
    "net_pnl",
    "r_multiple",
    "exit",
    "future",
    "mfe",
    "mae",
    "outcome",
    "target",
    "y_",
)

# Single-timeframe feature columns, in the order defined by the v1.5 spec.
SINGLE_TF_FEATURES: tuple[str, ...] = (
    # price / return
    "close",
    "log_return_1",
    "log_return_3",
    "log_return_5",
    "log_return_10",
    "log_return_20",
    "close_position_in_20bar_range",
    "close_position_in_50bar_range",
    "distance_from_20bar_high_atr",
    "distance_from_20bar_low_atr",
    "distance_from_50bar_high_atr",
    "distance_from_50bar_low_atr",
    # volatility
    "atr_14",
    "atr_14_pct",
    "atr_50",
    "atr_50_pct",
    "atr_ratio_14_50",
    "rolling_return_std_20",
    "rolling_return_std_50",
    "candle_range_atr",
    "body_size_atr",
    "upper_wick_atr",
    "lower_wick_atr",
    # trend
    "ema_20",
    "ema_50",
    "ema_100",
    "ema_200",
    "close_above_ema_20",
    "close_above_ema_50",
    "close_above_ema_100",
    "close_above_ema_200",
    "ema_20_slope_5_atr",
    "ema_50_slope_10_atr",
    "ema_100_slope_20_atr",
    "distance_close_ema_20_atr",
    "distance_close_ema_50_atr",
    "distance_close_ema_100_atr",
    "distance_close_ema_200_atr",
    # momentum
    "rsi_14",
    "rsi_28",
    "macd_line",
    "macd_signal",
    "macd_hist",
    "adx_14",
    "plus_di_14",
    "minus_di_14",
    # breakout / regime
    "donchian_20_position",
    "donchian_55_position",
    "donchian_100_position",
    "bars_since_20bar_high",
    "bars_since_55bar_high",
    "bars_since_100bar_high",
    "bollinger_bandwidth_20",
    "bollinger_percent_b_20",
    # supertrend-specific
    "supertrend_direction",
    "supertrend_distance_atr",
    "bars_since_supertrend_flip",
    # time
    "hour_utc",
    "day_of_week",
    "month",
    "quarter",
    "is_monday",
    "is_friday",
)

# A curated subset of single-timeframe features merged from higher timeframes.
# Time-of-day style features are intentionally excluded (meaningless on D1) and
# raw absolute levels are kept minimal -- the goal is *regime context*, not a
# duplicate of the full feature block.
HTF_FEATURE_SUBSET: tuple[str, ...] = (
    "close",
    "log_return_5",
    "log_return_20",
    "atr_14_pct",
    "atr_ratio_14_50",
    "rolling_return_std_20",
    "rsi_14",
    "macd_hist",
    "adx_14",
    "ema_50_slope_10_atr",
    "distance_close_ema_50_atr",
    "distance_close_ema_200_atr",
    "close_above_ema_50",
    "close_above_ema_200",
    "close_position_in_50bar_range",
    "donchian_55_position",
    "supertrend_direction",
    "supertrend_distance_atr",
)

# Higher-timeframe bar durations (used for the conservative completeness cutoff).
_TIMEFRAME_DURATION: dict[str, pd.Timedelta] = {
    "M5": pd.Timedelta(minutes=5),
    "M15": pd.Timedelta(minutes=15),
    "H1": pd.Timedelta(hours=1),
    "H4": pd.Timedelta(hours=4),
    "D1": pd.Timedelta(days=1),
}


def timeframe_duration(timeframe: str) -> pd.Timedelta:
    """Bar duration for a MT5 timeframe string (e.g. ``"H4"`` -> 4 hours)."""
    try:
        return _TIMEFRAME_DURATION[timeframe.upper()]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unknown timeframe duration for {timeframe!r}") from exc


def to_naive_utc(series: pd.Series) -> pd.Series:
    """Return a tz-naive UTC datetime Series (matches risk_backtester times).

    The backtester strips the timezone after converting to UTC, so signal/entry
    timestamps in the trade frames are tz-naive UTC wall time. We normalise all
    join keys to the same representation.
    """
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        return series.dt.tz_convert("UTC").dt.tz_localize(None)
    return pd.to_datetime(series)


# ---------------------------------------------------------------------------
# Small rolling helpers
# ---------------------------------------------------------------------------

def _bars_since_rolling_high(high: pd.Series, window: int) -> pd.Series:
    """Number of bars since the highest high within the trailing ``window``.

    ``0`` means the current (signal) bar is the window high. Uses a backward
    rolling window only, so it never sees future bars.
    """

    def _since(arr: np.ndarray) -> float:
        # np.argmax returns the first max; counting from the window end gives the
        # bars elapsed since that high was printed.
        return float((len(arr) - 1) - int(np.argmax(arr)))

    return high.rolling(window, min_periods=window).apply(_since, raw=True)


def _bars_since_supertrend_flip(direction: pd.Series) -> pd.Series:
    """Bars elapsed since the SuperTrend direction last changed (0 on a flip)."""
    flip = direction.ne(direction.shift(1))
    groups = flip.cumsum()
    return direction.groupby(groups).cumcount().astype(float)


# ---------------------------------------------------------------------------
# Feature builder
# ---------------------------------------------------------------------------

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the single-timeframe feature block for an OHLC frame.

    Returns a DataFrame row-aligned to ``df`` (same length / order) carrying a
    tz-naive ``datetime`` column plus every name in :data:`SINGLE_TF_FEATURES`.
    Every column is a backward-looking value, so the row at the signal bar is
    safe to use as a feature (no lookahead).
    """
    out = pd.DataFrame(index=df.index)
    out["datetime"] = to_naive_utc(df["datetime"]).to_numpy()

    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    close = df["close"]

    atr_14 = indicators.atr(df, 14)
    atr_50 = indicators.atr(df, 50)

    # --- price / return ----------------------------------------------------
    out["close"] = close
    log_close = np.log(close)
    for k in (1, 3, 5, 10, 20):
        out[f"log_return_{k}"] = log_close - log_close.shift(k)

    for window in (20, 50):
        roll_high = high.rolling(window, min_periods=window).max()
        roll_low = low.rolling(window, min_periods=window).min()
        span = (roll_high - roll_low).replace(0.0, np.nan)
        out[f"close_position_in_{window}bar_range"] = (close - roll_low) / span
        # distance_from_high <= 0 (price below the high); distance_from_low >= 0.
        out[f"distance_from_{window}bar_high_atr"] = (close - roll_high) / atr_14
        out[f"distance_from_{window}bar_low_atr"] = (close - roll_low) / atr_14

    # --- volatility --------------------------------------------------------
    out["atr_14"] = atr_14
    out["atr_14_pct"] = atr_14 / close * 100.0
    out["atr_50"] = atr_50
    out["atr_50_pct"] = atr_50 / close * 100.0
    out["atr_ratio_14_50"] = atr_14 / atr_50.replace(0.0, np.nan)

    log_ret_1 = log_close - log_close.shift(1)
    out["rolling_return_std_20"] = log_ret_1.rolling(20, min_periods=20).std()
    out["rolling_return_std_50"] = log_ret_1.rolling(50, min_periods=50).std()

    out["candle_range_atr"] = (high - low) / atr_14
    out["body_size_atr"] = (close - open_).abs() / atr_14
    # Wicks of the *signal* candle (a completed bar) -- not the entry candle.
    out["upper_wick_atr"] = (high - np.maximum(open_, close)) / atr_14
    out["lower_wick_atr"] = (np.minimum(open_, close) - low) / atr_14

    # --- trend -------------------------------------------------------------
    ema_periods = (20, 50, 100, 200)
    emas = {p: indicators.ema(close, p) for p in ema_periods}
    for p in ema_periods:
        out[f"ema_{p}"] = emas[p]
        out[f"close_above_ema_{p}"] = (close > emas[p]).astype(float)
        out[f"distance_close_ema_{p}_atr"] = (close - emas[p]) / atr_14
    out["ema_20_slope_5_atr"] = (emas[20] - emas[20].shift(5)) / atr_14
    out["ema_50_slope_10_atr"] = (emas[50] - emas[50].shift(10)) / atr_14
    out["ema_100_slope_20_atr"] = (emas[100] - emas[100].shift(20)) / atr_14

    # --- momentum ----------------------------------------------------------
    out["rsi_14"] = indicators.rsi(close, 14)
    out["rsi_28"] = indicators.rsi(close, 28)
    macd_line = indicators.ema(close, 12) - indicators.ema(close, 26)
    macd_signal = indicators.ema(macd_line, 9)
    out["macd_line"] = macd_line
    out["macd_signal"] = macd_signal
    out["macd_hist"] = macd_line - macd_signal
    adx_df = indicators.adx(df, 14)
    out["adx_14"] = adx_df["adx"]
    out["plus_di_14"] = adx_df["plus_di"]
    out["minus_di_14"] = adx_df["minus_di"]

    # --- breakout / regime -------------------------------------------------
    for window in (20, 55, 100):
        channel = indicators.donchian_channel(df, lookback=window)
        span = (channel["upper"] - channel["lower"]).replace(0.0, np.nan)
        out[f"donchian_{window}_position"] = (close - channel["lower"]) / span
        out[f"bars_since_{window}bar_high"] = _bars_since_rolling_high(high, window)

    bb_mid = close.rolling(20, min_periods=20).mean()
    bb_std = close.rolling(20, min_periods=20).std(ddof=0)
    bb_upper = bb_mid + 2.0 * bb_std
    bb_lower = bb_mid - 2.0 * bb_std
    bb_span = (bb_upper - bb_lower).replace(0.0, np.nan)
    out["bollinger_bandwidth_20"] = (bb_upper - bb_lower) / bb_mid.replace(0.0, np.nan)
    out["bollinger_percent_b_20"] = (close - bb_lower) / bb_span

    # --- supertrend-specific (reference regime, fixed params) --------------
    st = indicators.supertrend(
        df, atr_period=_ST_REF_ATR_PERIOD, multiplier=_ST_REF_MULTIPLIER
    )
    out["supertrend_direction"] = st["direction"].astype(float)
    out["supertrend_distance_atr"] = (close - st["supertrend"]) / atr_14
    out["bars_since_supertrend_flip"] = _bars_since_supertrend_flip(st["direction"])

    # --- time --------------------------------------------------------------
    dt = pd.DatetimeIndex(out["datetime"])
    out["hour_utc"] = dt.hour.astype(float)
    out["day_of_week"] = dt.dayofweek.astype(float)
    out["month"] = dt.month.astype(float)
    out["quarter"] = dt.quarter.astype(float)
    out["is_monday"] = (dt.dayofweek == 0).astype(float)
    out["is_friday"] = (dt.dayofweek == 4).astype(float)

    # Replace +/-inf produced by zero-span divisions with NaN (CatBoost handles
    # missing values natively; infinities would poison tree splits).
    feature_cols = list(SINGLE_TF_FEATURES)
    out[feature_cols] = out[feature_cols].replace([np.inf, -np.inf], np.nan)
    return out[["datetime", *feature_cols]]


def build_htf_feature_table(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the higher-timeframe subset features for a HTF OHLC frame.

    Returns a DataFrame with a tz-naive ``datetime`` column plus the columns in
    :data:`HTF_FEATURE_SUBSET` (un-suffixed; the suffix is applied at merge time).
    """
    feats = compute_features(df)
    return feats[["datetime", *HTF_FEATURE_SUBSET]].copy()


def merge_htf_features(
    base: pd.DataFrame,
    htf_table: pd.DataFrame,
    *,
    suffix: str,
    htf_duration: pd.Timedelta,
    signal_time_col: str = "signal_time",
) -> pd.DataFrame:
    """Backward ``asof`` merge of higher-timeframe features onto signal rows.

    No-leakage rule (conservative, per the v1.5 spec): a higher-timeframe row is
    eligible only when its ``datetime`` is ``<= signal_time - htf_duration``. The
    HTF candle is labelled by its *open* time, so subtracting one full HTF bar
    duration guarantees the merged candle is completely closed before the signal
    bar -- an incomplete HTF candle can never leak in.

    ``base`` must contain ``signal_time_col`` (tz-naive UTC). The returned frame
    preserves ``base``'s original row order and adds ``<feat>_<suffix>`` columns.
    """
    if base.empty:
        suffixed = {c: f"{c}_{suffix}" for c in HTF_FEATURE_SUBSET}
        return base.assign(**{v: np.nan for v in suffixed.values()})

    left = base.copy()
    left["_orig_order"] = np.arange(len(left))
    left["_merge_key"] = (
        pd.to_datetime(left[signal_time_col]) - htf_duration
    )
    left = left.sort_values("_merge_key")

    right = htf_table.copy()
    right["datetime"] = pd.to_datetime(right["datetime"])
    right = right.sort_values("datetime").rename(
        columns={c: f"{c}_{suffix}" for c in HTF_FEATURE_SUBSET}
    )

    merged = pd.merge_asof(
        left,
        right,
        left_on="_merge_key",
        right_on="datetime",
        direction="backward",
        suffixes=("", f"_{suffix}_dt"),
    )
    merged = merged.drop(columns=["_merge_key", "datetime"], errors="ignore")
    merged = merged.sort_values("_orig_order").drop(columns="_orig_order")
    return merged.reset_index(drop=True)


def feature_columns_for_finalist(finalist: str) -> list[str]:
    """Ordered feature columns a given finalist's model consumes.

    D (H4 SuperTrend) merges only D1 context; C (H1 Donchian) merges H4 and D1.
    """
    cols = list(SINGLE_TF_FEATURES)
    if finalist.upper() == "C":
        cols += [f"{c}_h4" for c in HTF_FEATURE_SUBSET]
    cols += [f"{c}_d1" for c in HTF_FEATURE_SUBSET]
    return cols


# ---------------------------------------------------------------------------
# Feature-set ablation modes (Strategy Lab v1.5.1)
# ---------------------------------------------------------------------------
# The v1.5 filter consumed *every* feature. v1.5.1 lets us train on curated
# subsets to isolate *why* the filter failed to transfer to the 2025-2026 test
# period -- in particular whether absolute price-level features (which sit far
# outside the 2015-2021 training price range) or unstable feature groups are to
# blame. The dataset still carries every column; a mode only changes which
# columns a given model is allowed to see (selection happens at train time).

FEATURE_SET_MODES: tuple[str, ...] = (
    "all_features",
    "no_absolute_price",
    "normalized_only",
    "no_higher_timeframe",
    "higher_timeframe_only",
    "no_time_features",
)

# Higher-timeframe suffixes appended at merge time.
HTF_SUFFIXES: tuple[str, ...] = ("_h4", "_d1")

# Raw, dimensioned price/level features. Their value lives in the absolute
# price domain, so a model trained on 2015-2021 levels cannot generalise to the
# far higher 2025-2026 levels. ``no_absolute_price`` drops these (and their
# higher-timeframe versions); the ``_pct`` / ``_atr`` / ``ratio`` derivatives
# of ATR are intentionally *kept* (they are dimensionless).
ABSOLUTE_PRICE_BASE_FEATURES: tuple[str, ...] = (
    "close",
    "ema_20",
    "ema_50",
    "ema_100",
    "ema_200",
    "atr_14",
    "atr_50",
)

# Calendar / session features (single-timeframe only; HTF context has none).
TIME_FEATURES: tuple[str, ...] = (
    "hour_utc",
    "day_of_week",
    "month",
    "quarter",
    "is_monday",
    "is_friday",
)

_ABSOLUTE_PRICE_SET = frozenset(ABSOLUTE_PRICE_BASE_FEATURES)
_TIME_FEATURE_SET = frozenset(TIME_FEATURES)


def strip_htf_suffix(column: str) -> str:
    """Return ``column`` without a trailing ``_h4`` / ``_d1`` suffix."""
    for suffix in HTF_SUFFIXES:
        if column.endswith(suffix):
            return column[: -len(suffix)]
    return column


def is_higher_timeframe_feature(column: str) -> bool:
    """True for merged higher-timeframe context columns (``*_h4`` / ``*_d1``)."""
    return column.endswith(HTF_SUFFIXES)


def is_absolute_price_feature(column: str) -> bool:
    """True for a raw price/level feature (any timeframe), e.g. ``close``,
    ``ema_200``, ``atr_14`` and their ``_h4`` / ``_d1`` versions. The base name
    is matched exactly so ATR derivatives (``atr_14_pct``, ``atr_ratio_14_50``,
    ``*_atr``) are *not* flagged as absolute."""
    return strip_htf_suffix(column) in _ABSOLUTE_PRICE_SET


def is_time_feature(column: str) -> bool:
    """True for a calendar / session feature."""
    return column in _TIME_FEATURE_SET


def is_normalized_feature(column: str) -> bool:
    """True for a dimensionless / normalised feature (``normalized_only`` mode).

    The base name (after stripping any HTF suffix) must match one of the
    normalised families in the v1.5.1 spec: log returns, ATR-percentages,
    ratios, range/channel positions, ATR-normalised distances & slopes,
    RSI/ADX/DI, close-above-EMA flags, bars-since counters, Bollinger
    percent-b / bandwidth, and the time features. Raw levels (``close``,
    ``ema_*``, ``atr_14/50``) and raw unnormalised MACD line/signal/hist are
    excluded because they are not dimensionless.
    """
    base = strip_htf_suffix(column)
    if base in _TIME_FEATURE_SET:
        return True
    if base.startswith(
        (
            "log_return_",
            "rsi_",
            "adx_",
            "plus_di_",
            "minus_di_",
            "close_above_ema_",
            "bars_since_",
            "bollinger_percent_b_",
            "bollinger_bandwidth_",
        )
    ):
        return True
    if base.endswith(("_pct", "_atr")):
        return True
    if "ratio" in base or "position" in base:
        return True
    return False


def select_feature_set(columns: Iterable[str], mode: str) -> list[str]:
    """Subset ``columns`` (in their original order) for one ablation ``mode``.

    ``columns`` is typically :func:`feature_columns_for_finalist` intersected
    with the columns actually present in the dataset. The returned list never
    re-orders or duplicates entries.
    """
    cols = list(columns)
    if mode == "all_features":
        return cols
    if mode == "no_absolute_price":
        return [c for c in cols if not is_absolute_price_feature(c)]
    if mode == "normalized_only":
        return [c for c in cols if is_normalized_feature(c)]
    if mode == "no_higher_timeframe":
        return [c for c in cols if not is_higher_timeframe_feature(c)]
    if mode == "higher_timeframe_only":
        return [c for c in cols if is_higher_timeframe_feature(c) or is_time_feature(c)]
    if mode == "no_time_features":
        return [c for c in cols if not is_time_feature(c)]
    raise ValueError(
        f"Unknown feature-set mode {mode!r}; expected one of {FEATURE_SET_MODES}"
    )


def feature_columns_for_finalist_mode(finalist: str, mode: str) -> list[str]:
    """Feature columns for ``finalist`` restricted to one ablation ``mode``."""
    return select_feature_set(feature_columns_for_finalist(finalist), mode)


def assert_feature_names_clean(columns: list[str]) -> None:
    """Raise if any feature column name looks like an outcome / target / future.

    This is a name-level guard against the most common leakage mistake: feeding
    a trade-outcome column into the model. It complements the structural checks
    in :mod:`ml_signal_filter`.
    """
    bad: list[str] = []
    for col in columns:
        lower = col.lower()
        # "y_" flags target columns (y_good_trade, y_profitable); match it only
        # as a name prefix so legitimate features like "body_size_atr" or
        # "day_of_week" (which merely contain "y_") are not false-positives.
        flagged = lower.startswith("y_") or any(
            token in lower
            for token in FORBIDDEN_FEATURE_SUBSTRINGS
            if token != "y_"
        )
        if flagged:
            bad.append(col)
    if bad:
        raise ValueError(
            "Feature columns contain forbidden outcome/target substrings: "
            f"{bad}"
        )
