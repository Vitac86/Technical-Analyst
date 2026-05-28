"""
Price action structure features derived from confirmed fractals.

All features use only current and past confirmed fractal data — no future leakage.
Call add_price_action_features_grouped() after add_confirmed_fractals_grouped().

Exported:
    PRICE_ACTION_FEATURE_COLUMNS   list of numeric feature column names
    add_price_action_features()    per-ticker (requires reset integer index)
    add_price_action_features_grouped()   multi-ticker wrapper
"""
import numpy as np
import pandas as pd

PRICE_ACTION_FEATURE_COLUMNS = [
    # Market structure
    "higher_high",
    "lower_high",
    "higher_low",
    "lower_low",
    "structure_trend",             # 1=bullish  0=neutral  -1=bearish
    # Structural breaks
    "break_of_structure_up",       # 1 on bar that first closes above last fractal high
    "break_of_structure_down",
    "change_of_character_up",      # BoS_up while structure is bearish
    "change_of_character_down",    # BoS_down while structure is bullish
    # Relative close position
    "close_above_last_fractal_high",
    "close_below_last_fractal_low",
    # Wick tests
    "wick_above_last_fractal_high",
    "wick_below_last_fractal_low",
    # Liquidity sweeps / false breakouts
    "sweep_high_reversal",         # high>fractal_high but close<fractal_high
    "sweep_low_reversal",          # low<fractal_low  but close>fractal_low
    # Range context
    "range_position_between_fractals",  # 0-1 within [last_low, last_high]
    "fractal_range_pct",                # (last_high-last_low)/close*100
    "compression_near_fractal_range",   # 1 if range_pct < 0.5%
    # Fractal timing (scale-free)
    "bars_since_fractal_high",
    "bars_since_fractal_low",
    "distance_to_last_fractal_high_pct",
    "distance_to_last_fractal_low_pct",
]

# Columns that must be present before calling add_price_action_features
_REQUIRED_FRACTAL_COLS = [
    "confirmed_fractal_high_price",
    "confirmed_fractal_low_price",
    "last_fractal_high",
    "last_fractal_low",
    "bars_since_fractal_high",
    "bars_since_fractal_low",
    "distance_to_last_fractal_high_pct",
    "distance_to_last_fractal_low_pct",
]


def add_price_action_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add price action columns to df.

    Requires add_confirmed_fractals() columns to be present.
    Call per-ticker with a reset integer index; do not mix tickers.
    """
    missing = [c for c in _REQUIRED_FRACTAL_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing fractal columns: {missing}. "
            "Run add_confirmed_fractals() first."
        )

    df = df.copy().reset_index(drop=True)
    c = df["close"]
    h = df["high"]
    l = df["low"]

    last_high = df["last_fractal_high"]
    last_low  = df["last_fractal_low"]
    confirmed_high = df["confirmed_fractal_high_price"]
    confirmed_low  = df["confirmed_fractal_low_price"]

    # ------------------------------------------------------------------
    # Previous confirmed fractal prices
    # At each new confirmation bar, record the last_fractal_* value from
    # the bar immediately before. Forward-fill to propagate between events.
    # ------------------------------------------------------------------
    prev_last_high_at_events = last_high.shift(1).where(confirmed_high.notna())
    prev_last_high = prev_last_high_at_events.ffill()

    prev_last_low_at_events = last_low.shift(1).where(confirmed_low.notna())
    prev_last_low = prev_last_low_at_events.ffill()

    # ------------------------------------------------------------------
    # Structure comparison (requires two confirmed fractals on each side)
    # ------------------------------------------------------------------
    both_highs_known = last_high.notna() & prev_last_high.notna()
    both_lows_known  = last_low.notna()  & prev_last_low.notna()

    higher_high = (last_high > prev_last_high).where(both_highs_known).fillna(False).astype(float)
    lower_high  = (last_high < prev_last_high).where(both_highs_known).fillna(False).astype(float)
    higher_low  = (last_low  > prev_last_low ).where(both_lows_known ).fillna(False).astype(float)
    lower_low   = (last_low  < prev_last_low ).where(both_lows_known ).fillna(False).astype(float)

    df["higher_high"] = higher_high
    df["lower_high"]  = lower_high
    df["higher_low"]  = higher_low
    df["lower_low"]   = lower_low

    # structure_trend: 1=bullish(HH+HL), -1=bearish(LH+LL), 0=neutral
    bullish = (higher_high == 1) & (higher_low == 1)
    bearish = (lower_high  == 1) & (lower_low  == 1)
    structure_trend = np.select([bullish, bearish], [1.0, -1.0], default=0.0)
    df["structure_trend"] = structure_trend

    # ------------------------------------------------------------------
    # Close position relative to confirmed fractal levels
    # ------------------------------------------------------------------
    close_above_high = (c > last_high).where(last_high.notna()).fillna(False).astype(float)
    close_below_low  = (c < last_low ).where(last_low.notna() ).fillna(False).astype(float)
    df["close_above_last_fractal_high"] = close_above_high
    df["close_below_last_fractal_low"]  = close_below_low

    # ------------------------------------------------------------------
    # Break of structure: first bar close crosses the fractal level
    # ------------------------------------------------------------------
    prev_above = close_above_high.shift(1).fillna(0.0)
    prev_below = close_below_low.shift(1).fillna(0.0)
    bos_up   = ((close_above_high == 1) & (prev_above == 0)).astype(float)
    bos_down = ((close_below_low  == 1) & (prev_below == 0)).astype(float)
    df["break_of_structure_up"]   = bos_up
    df["break_of_structure_down"] = bos_down

    # Change of character: BoS against the current trend
    df["change_of_character_up"]   = (bos_up   * (pd.Series(structure_trend) == -1).astype(float).values)
    df["change_of_character_down"] = (bos_down * (pd.Series(structure_trend) ==  1).astype(float).values)

    # ------------------------------------------------------------------
    # Wick tests
    # ------------------------------------------------------------------
    df["wick_above_last_fractal_high"] = (
        (h > last_high).where(last_high.notna()).fillna(False).astype(float)
    )
    df["wick_below_last_fractal_low"] = (
        (l < last_low).where(last_low.notna()).fillna(False).astype(float)
    )

    # ------------------------------------------------------------------
    # Liquidity sweeps / false breakouts
    # sweep_high_reversal: high > last_high but close < last_high
    # sweep_low_reversal:  low  < last_low  but close > last_low
    # ------------------------------------------------------------------
    df["sweep_high_reversal"] = (
        (h > last_high) & (c < last_high)
    ).where(last_high.notna()).fillna(False).astype(float)

    df["sweep_low_reversal"] = (
        (l < last_low) & (c > last_low)
    ).where(last_low.notna()).fillna(False).astype(float)

    # ------------------------------------------------------------------
    # Range context
    # ------------------------------------------------------------------
    fractal_range = last_high - last_low
    both_levels = last_high.notna() & last_low.notna()

    df["fractal_range_pct"] = (
        (fractal_range / c * 100)
        .where(both_levels & (c > 0) & (fractal_range > 0))
    )

    df["range_position_between_fractals"] = (
        ((c - last_low) / fractal_range)
        .where(both_levels & (fractal_range > 0))
        .clip(0.0, 1.0)
    )

    df["compression_near_fractal_range"] = (
        (df["fractal_range_pct"] < 0.5).where(df["fractal_range_pct"].notna()).fillna(False).astype(float)
    )

    # ------------------------------------------------------------------
    # Pass through fractal timing columns (already computed)
    # ------------------------------------------------------------------
    # bars_since_fractal_high/low and distance_to_last_fractal_*_pct
    # are added by add_confirmed_fractals(); they are already in df.

    return df


def add_price_action_features_grouped(
    df: pd.DataFrame,
    ticker_col: str = "ticker",
) -> pd.DataFrame:
    """
    Apply add_price_action_features per ticker and re-assemble in original order.
    """
    df = df.copy()
    df["_pa_sort"] = np.arange(len(df))

    frames = []
    for _, grp in df.groupby(ticker_col, sort=False):
        grp = grp.sort_values("datetime").reset_index(drop=True)
        grp = add_price_action_features(grp)
        frames.append(grp)

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values("_pa_sort").reset_index(drop=True)
    return result.drop(columns=["_pa_sort"])
