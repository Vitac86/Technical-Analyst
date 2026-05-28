"""
Label creation for UP / DOWN / FLAT direction prediction.

Close-based mode: compares close[i + horizon] to close[i].
TP/SL path-based mode: placeholder for future implementation.
"""
import numpy as np
import pandas as pd

CLASS_NAMES = ["DOWN", "FLAT", "UP"]

# Integer encoding used inside the model and dataset
CLASS_TO_INT = {"DOWN": 0, "FLAT": 1, "UP": 2}
INT_TO_CLASS = {0: "DOWN", 1: "FLAT", 2: "UP"}

LABEL_COL = "label"


def create_labels_close(
    df: pd.DataFrame,
    horizon_candles: int = 3,
    up_threshold_pct: float = 0.25,
    down_threshold_pct: float = 0.25,
) -> pd.DataFrame:
    """
    Assign integer labels based on forward close return.

    For candle i:
        forward_return = (close[i + horizon] / close[i] - 1) * 100
        UP   (2)  if forward_return >= up_threshold_pct
        DOWN (0)  if forward_return <= -down_threshold_pct
        FLAT (1)  otherwise

    Rows where future close is unavailable (last horizon_candles rows, or any gap)
    receive NaN and should be dropped before training.
    """
    df = df.copy()
    future_close = df["close"].shift(-horizon_candles)
    forward_return = (future_close / df["close"] - 1) * 100

    conditions = [
        forward_return >= up_threshold_pct,
        forward_return <= -down_threshold_pct,
    ]
    choices = [CLASS_TO_INT["UP"], CLASS_TO_INT["DOWN"]]
    df[LABEL_COL] = np.select(conditions, choices, default=CLASS_TO_INT["FLAT"])

    # Rows where future close is unavailable get NaN (not FLAT)
    df.loc[future_close.isna(), LABEL_COL] = np.nan

    return df


def create_labels_tp_sl(
    df: pd.DataFrame,
    horizon_candles: int = 10,
    tp_pct: float = 0.5,
    sl_pct: float = 0.25,
) -> pd.DataFrame:
    """
    TP/SL path-based labels — not yet implemented.

    Future logic:
        LONG (2) if TP (+tp_pct%) is hit before SL (-sl_pct%) within horizon candles.
        SHORT (0) if downside TP hit before upside SL.
        FLAT (1) otherwise.
    """
    raise NotImplementedError(
        "TP/SL label mode is not yet implemented. Use create_labels_close instead."
    )
