"""
Label creation for UP / DOWN / FLAT direction prediction.

Two modes:
  close  — compares close[i + horizon] to close[i]
  tp_sl  — path-based: UP if TP hit before SL within horizon candles

Future return columns stored alongside labels for signal evaluation:
  future_close_return_pct  — (close[i+horizon] / close[i] - 1) * 100
  future_max_return_pct    — (max high over horizon / close[i] - 1) * 100
  future_min_return_pct    — (min low over horizon / close[i] - 1) * 100
"""
import numpy as np
import pandas as pd

CLASS_NAMES = ["DOWN", "FLAT", "UP"]

CLASS_TO_INT = {"DOWN": 0, "FLAT": 1, "UP": 2}
INT_TO_CLASS = {0: "DOWN", 1: "FLAT", 2: "UP"}

CLASS_ID_TO_NAME = INT_TO_CLASS
CLASS_NAME_TO_ID = CLASS_TO_INT

LABEL_COL = "label"
FUTURE_CLOSE_RETURN_COL = "future_close_return_pct"
FUTURE_MAX_RETURN_COL = "future_max_return_pct"
FUTURE_MIN_RETURN_COL = "future_min_return_pct"

FUTURE_RETURN_COLS = [
    FUTURE_CLOSE_RETURN_COL,
    FUTURE_MAX_RETURN_COL,
    FUTURE_MIN_RETURN_COL,
]


def _add_future_returns(df: pd.DataFrame, horizon_candles: int) -> pd.DataFrame:
    """Add future return columns. Last horizon_candles rows will have NaN."""
    c = df["close"]
    future_close = c.shift(-horizon_candles)
    df[FUTURE_CLOSE_RETURN_COL] = (future_close / c - 1) * 100

    # Rolling max high and min low over the NEXT horizon candles
    # shift(-1) aligns: for candle i, window covers [i+1 .. i+horizon]
    future_max = df["high"].shift(-1).rolling(horizon_candles).max().shift(-(horizon_candles - 1))
    future_min = df["low"].shift(-1).rolling(horizon_candles).min().shift(-(horizon_candles - 1))
    df[FUTURE_MAX_RETURN_COL] = (future_max / c - 1) * 100
    df[FUTURE_MIN_RETURN_COL] = (future_min / c - 1) * 100
    return df


def create_labels_close(
    df: pd.DataFrame,
    horizon_candles: int = 3,
    up_threshold_pct: float = 0.25,
    down_threshold_pct: float = 0.25,
    store_future_returns: bool = True,
) -> pd.DataFrame:
    """
    Assign integer labels based on forward close return.

    UP (2)   if (close[i+horizon]/close[i]-1)*100 >= up_threshold_pct
    DOWN (0) if (close[i+horizon]/close[i]-1)*100 <= -down_threshold_pct
    FLAT (1) otherwise

    Rows lacking future close data receive NaN label and must be dropped before training.
    """
    df = df.copy()

    if store_future_returns:
        df = _add_future_returns(df, horizon_candles)

    future_close = df["close"].shift(-horizon_candles)
    forward_return = (future_close / df["close"] - 1) * 100

    conditions = [
        forward_return >= up_threshold_pct,
        forward_return <= -down_threshold_pct,
    ]
    choices = [CLASS_TO_INT["UP"], CLASS_TO_INT["DOWN"]]
    df[LABEL_COL] = np.select(conditions, choices, default=CLASS_TO_INT["FLAT"])
    df.loc[future_close.isna(), LABEL_COL] = np.nan

    return df


def create_labels_tp_sl(
    df: pd.DataFrame,
    horizon_candles: int = 6,
    take_profit_pct: float = 0.30,
    stop_loss_pct: float = 0.20,
    flat_if_both_hit_same_candle: bool = True,
    store_future_returns: bool = True,
) -> pd.DataFrame:
    """
    Path-based TP/SL labels.

    For candle i, inspect candles i+1 .. i+horizon_candles.
    UP (2)   if +take_profit_pct% is reached before -stop_loss_pct%
    DOWN (0) if -take_profit_pct% is reached before +stop_loss_pct%
    FLAT (1) if neither side is triggered, or both hit in the same candle
             when flat_if_both_hit_same_candle=True.

    Labels look into the future (they are targets, not features) — no leakage.
    Last horizon_candles rows receive NaN and must be dropped before training.
    """
    df = df.copy()
    n = len(df)

    if store_future_returns:
        df = _add_future_returns(df, horizon_candles)

    labels = np.full(n, np.nan)
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values

    tp_mult = 1 + take_profit_pct / 100
    sl_mult = 1 - stop_loss_pct / 100

    for i in range(n - horizon_candles):
        entry = closes[i]
        tp_long = entry * tp_mult
        sl_long = entry * sl_mult
        tp_short = entry * sl_mult   # price drop = TP for short
        sl_short = entry * tp_mult   # price rise = SL for short

        result = CLASS_TO_INT["FLAT"]

        for j in range(i + 1, i + 1 + horizon_candles):
            h = highs[j]
            l = lows[j]

            long_tp_hit = h >= tp_long
            long_sl_hit = l <= sl_long
            short_tp_hit = l <= tp_short
            short_sl_hit = h >= sl_short

            if long_tp_hit and short_tp_hit:
                if flat_if_both_hit_same_candle:
                    result = CLASS_TO_INT["FLAT"]
                    break
                # Approximate by mid: closer side wins
                result = CLASS_TO_INT["UP"] if (h - tp_long) >= (tp_short - l) else CLASS_TO_INT["DOWN"]
                break
            if long_tp_hit:
                result = CLASS_TO_INT["UP"]
                break
            if short_tp_hit:
                result = CLASS_TO_INT["DOWN"]
                break
            # SL hits before TP → opposite side's TP never reached in this horizon
            if long_sl_hit:
                result = CLASS_TO_INT["FLAT"]
                break
            if short_sl_hit:
                result = CLASS_TO_INT["FLAT"]
                break

        labels[i] = result

    df[LABEL_COL] = labels
    return df
