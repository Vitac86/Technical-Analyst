from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.candle import Candle
from app.db.models.indicator_value import IndicatorValue
from app.schemas.levels import TechnicalLevel, TechnicalLevelsResponse


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_candles(
    db: Session,
    instrument_id: int,
    timeframe: str,
    lookback: int,
) -> list[Candle]:
    stmt = (
        select(Candle)
        .where(Candle.instrument_id == instrument_id, Candle.timeframe == timeframe)
        .order_by(Candle.timestamp.desc())
        .limit(lookback)
    )
    rows = list(db.execute(stmt).scalars().all())
    rows.reverse()  # oldest first
    return rows


def _load_latest_atr(
    db: Session,
    instrument_id: int,
    timeframe: str,
) -> float | None:
    stmt = (
        select(IndicatorValue)
        .where(
            IndicatorValue.instrument_id == instrument_id,
            IndicatorValue.timeframe == timeframe,
            IndicatorValue.indicator_name == "atr_14",
        )
        .order_by(IndicatorValue.timestamp.desc())
        .limit(1)
    )
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    raw = row.values.get("value")
    return float(raw) if raw is not None else None


# ---------------------------------------------------------------------------
# ATR fallback computation from raw candles
# ---------------------------------------------------------------------------


def _compute_atr_from_candles(candles: list[Candle], window: int = 14) -> float | None:
    if len(candles) < 2:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        prev_close = float(candles[i - 1].close)
        high = float(candles[i].high)
        low = float(candles[i].low)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return None
    recent = trs[-window:]
    return sum(recent) / len(recent)


# ---------------------------------------------------------------------------
# Pivot detection
# ---------------------------------------------------------------------------


def _find_pivot_lows(candles: list[Candle]) -> list[float]:
    pivots: list[float] = []
    for i in range(2, len(candles) - 2):
        low = float(candles[i].low)
        if (
            low < float(candles[i - 2].low)
            and low < float(candles[i - 1].low)
            and low < float(candles[i + 1].low)
            and low < float(candles[i + 2].low)
        ):
            pivots.append(low)
    return pivots


def _find_pivot_highs(candles: list[Candle]) -> list[float]:
    pivots: list[float] = []
    for i in range(2, len(candles) - 2):
        high = float(candles[i].high)
        if (
            high > float(candles[i - 2].high)
            and high > float(candles[i - 1].high)
            and high > float(candles[i + 1].high)
            and high > float(candles[i + 2].high)
        ):
            pivots.append(high)
    return pivots


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _distance(price: float, last_close: float) -> float:
    if last_close == 0:
        return 0.0
    return (price - last_close) / last_close * 100


def _build_summary(
    last_close: float,
    support: float | None,
    resistance: float | None,
    atr: float | None,
) -> str:
    if support is None and resistance is None:
        return "Insufficient data to determine price position within range."

    dist_sup = abs(_distance(support, last_close)) if support is not None else None
    dist_res = abs(_distance(resistance, last_close)) if resistance is not None else None

    # Proximity threshold: 1 ATR expressed as %, floor at 0.5 %
    atr_pct = (atr / last_close * 100) if (atr and last_close) else 1.0
    threshold = max(atr_pct, 0.5)

    if dist_res is not None and dist_res <= threshold:
        return "Price is trading near resistance; upside target is limited unless resistance breaks."
    if dist_sup is not None and dist_sup <= threshold:
        return "Price is near support; downside risk should be watched."

    if dist_sup is not None and dist_res is not None:
        if dist_sup < dist_res:
            return "Price is closer to support than resistance; downside risk should be watched."
        if dist_res < dist_sup:
            return "Price is closer to resistance than support; upside momentum may be limited."
        return "Price is mid-range between support and resistance."

    return "Price is mid-range between support and resistance."


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_technical_levels(
    db: Session,
    instrument_id: int,
    timeframe: str,
    lookback: int = 100,
) -> TechnicalLevelsResponse:
    now = datetime.now(timezone.utc)

    candles = _load_candles(db, instrument_id, timeframe, lookback)

    if not candles:
        return TechnicalLevelsResponse(
            instrument_id=instrument_id,
            timeframe=timeframe,
            last_close=None,
            atr=None,
            atr_percent=None,
            lookback=lookback,
            levels=[],
            summary="No data available.",
            generated_at=now,
            message="No candles available.",
        )

    if len(candles) < 10:
        return TechnicalLevelsResponse(
            instrument_id=instrument_id,
            timeframe=timeframe,
            last_close=None,
            atr=None,
            atr_percent=None,
            lookback=lookback,
            levels=[],
            summary="Not enough data to calculate levels.",
            generated_at=now,
            message="Not enough candles to calculate levels.",
        )

    last_close = float(candles[-1].close)
    recent_low = min(float(c.low) for c in candles)
    recent_high = max(float(c.high) for c in candles)

    pivot_lows = _find_pivot_lows(candles)
    pivot_highs = _find_pivot_highs(candles)

    # Nearest support: highest pivot low below last_close, else recent_low
    supports_below = [p for p in pivot_lows if p < last_close]
    nearest_support = max(supports_below) if supports_below else recent_low

    # Nearest resistance: lowest pivot high above last_close, else recent_high
    resistances_above = [p for p in pivot_highs if p > last_close]
    nearest_resistance = min(resistances_above) if resistances_above else recent_high

    # ATR — try stored indicator first, fall back to manual computation
    atr = _load_latest_atr(db, instrument_id, timeframe)
    atr_source = "indicator"
    if atr is None:
        atr = _compute_atr_from_candles(candles)
        atr_source = "candles"

    atr_percent = (atr / last_close * 100) if (atr and last_close) else None

    levels: list[TechnicalLevel] = []

    levels.append(
        TechnicalLevel(
            kind="support",
            label="Nearest Support",
            price=nearest_support,
            distance_percent=_distance(nearest_support, last_close),
            reason=(
                "Pivot low cluster below current price"
                if supports_below
                else "Recent range low (no pivot lows found in lookback window)"
            ),
        )
    )

    levels.append(
        TechnicalLevel(
            kind="resistance",
            label="Nearest Resistance",
            price=nearest_resistance,
            distance_percent=_distance(nearest_resistance, last_close),
            reason=(
                "Pivot high cluster above current price"
                if resistances_above
                else "Recent range high (no pivot highs found in lookback window)"
            ),
        )
    )

    if atr is not None:
        atr_note = f"ATR 14 from {atr_source}"

        # Target Up 1 — nearest resistance if above close, else 1×ATR
        tu1_price = nearest_resistance if nearest_resistance > last_close else last_close + atr
        tu1_reason = (
            "Nearest resistance above price (first upside scenario level)"
            if nearest_resistance > last_close
            else f"1× ATR above current price — no resistance found above ({atr_note})"
        )
        levels.append(
            TechnicalLevel(
                kind="target_up",
                label="Target Up 1",
                price=tu1_price,
                distance_percent=_distance(tu1_price, last_close),
                reason=tu1_reason,
            )
        )

        # Target Up 2 — always 2×ATR
        tu2_price = last_close + 2 * atr
        levels.append(
            TechnicalLevel(
                kind="target_up",
                label="Target Up 2",
                price=tu2_price,
                distance_percent=_distance(tu2_price, last_close),
                reason=f"Estimated level: 2× ATR above current price ({atr_note})",
            )
        )

        # Target Down 1 — nearest support if below close, else 1×ATR
        td1_price = nearest_support if nearest_support < last_close else last_close - atr
        td1_reason = (
            "Nearest support below price (first downside scenario level)"
            if nearest_support < last_close
            else f"1× ATR below current price — no support found below ({atr_note})"
        )
        levels.append(
            TechnicalLevel(
                kind="target_down",
                label="Target Down 1",
                price=td1_price,
                distance_percent=_distance(td1_price, last_close),
                reason=td1_reason,
            )
        )

        # Stop zone — 1.5×ATR below close
        stop_price = last_close - 1.5 * atr
        atr_pct_str = f"{atr_percent:.2f}%" if atr_percent is not None else "N/A"
        levels.append(
            TechnicalLevel(
                kind="stop_zone",
                label="Stop Zone",
                price=stop_price,
                distance_percent=_distance(stop_price, last_close),
                reason=f"Estimated stop zone: 1.5× ATR below current price ({atr_note}, ATR%={atr_pct_str})",
            )
        )

        # ATR info row
        levels.append(
            TechnicalLevel(
                kind="info",
                label="ATR 14",
                price=round(atr, 4),
                distance_percent=None,
                reason=(
                    f"Average True Range (14 periods, source: {atr_source})"
                    + (f" — {atr_pct_str} of price" if atr_percent is not None else "")
                ),
            )
        )
    else:
        levels.append(
            TechnicalLevel(
                kind="info",
                label="ATR Unavailable",
                price=None,
                distance_percent=None,
                reason="ATR could not be computed; targets require ATR. Support/resistance levels are shown above.",
            )
        )

    summary = _build_summary(last_close, nearest_support, nearest_resistance, atr)

    message: str | None = None
    if atr is None:
        message = "ATR unavailable; showing support/resistance levels only."

    return TechnicalLevelsResponse(
        instrument_id=instrument_id,
        timeframe=timeframe,
        last_close=last_close,
        atr=atr,
        atr_percent=atr_percent,
        lookback=len(candles),
        levels=levels,
        summary=summary,
        generated_at=now,
        message=message,
    )
