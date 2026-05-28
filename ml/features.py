"""
Technical feature calculation from OHLCV candle data.

All features use only past and current candle data — no future leakage.
MACD and ATR are normalized by close price so they are scale-invariant
across tickers with different price levels.

FEATURE_COLUMNS defines the ordered list used by both build_dataset and train_catboost.
"""
import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    # Short-term returns (%)
    "return_1",
    "return_3",
    "return_5",
    "return_10",
    "return_20",
    # Rolling volatility: std of log returns (%)
    "volatility_10",
    "volatility_20",
    # Candle shape — all in % of range/price
    "candle_body_pct",
    "candle_range_pct",
    "upper_wick_pct",
    "lower_wick_pct",
    # Volume
    "volume_change_5",
    "volume_zscore_20",
    # Price vs SMA (%)
    "price_vs_sma_10",
    "price_vs_sma_20",
    "price_vs_sma_50",
    # Price vs EMA (%)
    "price_vs_ema_10",
    "price_vs_ema_20",
    "price_vs_ema_50",
    # MA slopes (% change over 5 bars)
    "sma_20_slope",
    "ema_20_slope",
    # Momentum
    "rsi_14",
    "macd_line",        # normalized by close (%)
    "macd_signal",
    "macd_hist",
    # Volatility
    "atr_14",           # normalized by close (%)
    # Bollinger Bands
    "bollinger_position_20",  # 0-1 within band
    "bollinger_width_20",     # band width / SMA (%)
    # Range position (0-1 within N-bar high/low range)
    "high_low_position_20",
    "high_low_position_50",
]


def calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all FEATURE_COLUMNS to a copy of the input DataFrame.

    Args:
        df: DataFrame with columns [open, high, low, close, volume].
            Must be sorted ascending by datetime, deduplicated.

    Returns:
        Input DataFrame with feature columns appended. NaN rows are NOT dropped here;
        call drop_invalid_feature_rows() separately after labelling.
    """
    df = df.copy()
    c = df["close"]
    o = df["open"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # --- Returns (%) ---
    for n in [1, 3, 5, 10, 20]:
        df[f"return_{n}"] = c.pct_change(n) * 100

    # --- Volatility: rolling std of log returns (%) ---
    log_ret = np.log(c / c.shift(1))
    df["volatility_10"] = log_ret.rolling(10).std() * 100
    df["volatility_20"] = log_ret.rolling(20).std() * 100

    # --- Candle shape ---
    hl = h - l
    upper_body = df[["open", "close"]].max(axis=1)
    lower_body = df[["open", "close"]].min(axis=1)

    df["candle_range_pct"] = (hl / o * 100).where(o > 0)
    df["candle_body_pct"] = ((c - o).abs() / hl * 100).where(hl > 0).fillna(50.0)
    df["upper_wick_pct"] = ((h - upper_body) / hl * 100).where(hl > 0).fillna(0.0)
    df["lower_wick_pct"] = ((lower_body - l) / hl * 100).where(hl > 0).fillna(0.0)

    # --- Volume ---
    vol_mean5 = v.rolling(5).mean()
    vol_mean20 = v.rolling(20).mean()
    vol_std20 = v.rolling(20).std()
    df["volume_change_5"] = ((v / vol_mean5 - 1) * 100).where(vol_mean5 > 0)
    df["volume_zscore_20"] = ((v - vol_mean20) / vol_std20).where(vol_std20 > 0)

    # --- Price vs SMA (%) ---
    for period in [10, 20, 50]:
        sma = c.rolling(period).mean()
        df[f"price_vs_sma_{period}"] = ((c / sma - 1) * 100).where(sma > 0)

    # --- Price vs EMA (%) ---
    for period in [10, 20, 50]:
        ema = c.ewm(span=period, adjust=False).mean()
        df[f"price_vs_ema_{period}"] = ((c / ema - 1) * 100).where(ema > 0)

    # --- MA slopes: % change over previous 5 bars ---
    sma20 = c.rolling(20).mean()
    ema20 = c.ewm(span=20, adjust=False).mean()
    df["sma_20_slope"] = ((sma20 / sma20.shift(5) - 1) * 100).where(sma20.shift(5) > 0)
    df["ema_20_slope"] = ((ema20 / ema20.shift(5) - 1) * 100).where(ema20.shift(5) > 0)

    # --- RSI(14) via Wilder's EMA (com = period - 1) ---
    delta = c.diff()
    avg_gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

    # --- MACD normalized by close (%) ---
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_norm = ((ema12 - ema26) / c * 100).where(c > 0)
    signal_norm = macd_norm.ewm(span=9, adjust=False).mean()
    df["macd_line"] = macd_norm
    df["macd_signal"] = signal_norm
    df["macd_hist"] = macd_norm - signal_norm

    # --- ATR(14) normalized by close (%) ---
    prev_close = c.shift(1)
    true_range = pd.DataFrame({
        "hl": h - l,
        "hc": (h - prev_close).abs(),
        "lc": (l - prev_close).abs(),
    }).max(axis=1)
    atr_raw = true_range.ewm(com=13, adjust=False).mean()
    df["atr_14"] = (atr_raw / c * 100).where(c > 0)

    # --- Bollinger Bands(20, 2) ---
    sma20_bb = c.rolling(20).mean()
    std20_bb = c.rolling(20).std()
    upper_bb = sma20_bb + 2 * std20_bb
    lower_bb = sma20_bb - 2 * std20_bb
    bb_range = upper_bb - lower_bb
    df["bollinger_position_20"] = ((c - lower_bb) / bb_range).where(bb_range > 0).fillna(0.5)
    df["bollinger_width_20"] = (bb_range / sma20_bb * 100).where(sma20_bb > 0)

    # --- High-Low range position (0-1) ---
    for period in [20, 50]:
        roll_high = h.rolling(period).max()
        roll_low = l.rolling(period).min()
        roll_range = roll_high - roll_low
        df[f"high_low_position_{period}"] = (
            ((c - roll_low) / roll_range).where(roll_range > 0).fillna(0.5)
        )

    return df


def drop_invalid_feature_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with NaN or inf in any FEATURE_COLUMNS column."""
    feat = df[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    bad = feat.isna().any(axis=1)
    n_dropped = bad.sum()
    if n_dropped > 0:
        print(f"  Dropping {n_dropped} rows with NaN/inf in features.")
    return df[~bad].reset_index(drop=True)
