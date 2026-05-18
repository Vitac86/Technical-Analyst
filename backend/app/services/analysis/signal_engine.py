from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.candle import Candle
from app.db.models.indicator_value import IndicatorValue
from app.schemas.analysis import (
    AggregateSignal,
    Confidence,
    TechnicalSignalAggregate,
    TechnicalSignalItem,
    TechnicalSignalResponse,
)


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def _load_latest_candle(
    db: Session,
    instrument_id: int,
    timeframe: str,
) -> Candle | None:
    stmt = (
        select(Candle)
        .where(Candle.instrument_id == instrument_id, Candle.timeframe == timeframe)
        .order_by(Candle.timestamp.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def _load_latest_indicator_rows(
    db: Session,
    instrument_id: int,
    timeframe: str,
    indicator_name: str,
    limit: int = 2,
) -> list[IndicatorValue]:
    stmt = (
        select(IndicatorValue)
        .where(
            IndicatorValue.instrument_id == instrument_id,
            IndicatorValue.timeframe == timeframe,
            IndicatorValue.indicator_name == indicator_name,
        )
        .order_by(IndicatorValue.timestamp.desc())
        .limit(limit)
    )
    rows = list(db.execute(stmt).scalars().all())
    rows.reverse()  # oldest first; rows[-1] is always the latest
    return rows


# ---------------------------------------------------------------------------
# Per-indicator evaluators
# ---------------------------------------------------------------------------


def _evaluate_sma(close: float, rows: list[IndicatorValue]) -> TechnicalSignalItem:
    if not rows:
        return TechnicalSignalItem(
            indicator_name="sma_20",
            label="SMA 20",
            value=None,
            signal="neutral",
            score=0,
            strength="weak",
            reason="No SMA 20 data available.",
            timestamp=None,
        )
    latest = rows[-1]
    raw = latest.values.get("value")
    if raw is None:
        return TechnicalSignalItem(
            indicator_name="sma_20",
            label="SMA 20",
            value=None,
            signal="neutral",
            score=0,
            strength="weak",
            reason="No SMA 20 value available.",
            timestamp=latest.timestamp,
        )
    sma_val = float(raw)
    diff_pct = (close - sma_val) / sma_val * 100 if sma_val != 0 else 0.0
    if diff_pct > 0.3:
        signal, score, strength, reason = "buy", 1, "medium", "Close is above SMA 20."
    elif diff_pct < -0.3:
        signal, score, strength, reason = "sell", -1, "medium", "Close is below SMA 20."
    else:
        signal, score, strength, reason = "neutral", 0, "weak", "Close is near SMA 20."
    return TechnicalSignalItem(
        indicator_name="sma_20",
        label="SMA 20",
        value={"sma": round(sma_val, 4), "close": round(close, 4), "diff_pct": round(diff_pct, 4)},
        signal=signal,
        score=score,
        strength=strength,
        reason=reason,
        timestamp=latest.timestamp,
    )


def _evaluate_ema(close: float, rows: list[IndicatorValue]) -> TechnicalSignalItem:
    if not rows:
        return TechnicalSignalItem(
            indicator_name="ema_20",
            label="EMA 20",
            value=None,
            signal="neutral",
            score=0,
            strength="weak",
            reason="No EMA 20 data available.",
            timestamp=None,
        )
    latest = rows[-1]
    raw = latest.values.get("value")
    if raw is None:
        return TechnicalSignalItem(
            indicator_name="ema_20",
            label="EMA 20",
            value=None,
            signal="neutral",
            score=0,
            strength="weak",
            reason="No EMA 20 value available.",
            timestamp=latest.timestamp,
        )
    ema_val = float(raw)
    diff_pct = (close - ema_val) / ema_val * 100 if ema_val != 0 else 0.0
    if diff_pct > 0.3:
        signal, score, strength, reason = "buy", 1, "medium", "Close is above EMA 20."
    elif diff_pct < -0.3:
        signal, score, strength, reason = "sell", -1, "medium", "Close is below EMA 20."
    else:
        signal, score, strength, reason = "neutral", 0, "weak", "Close is near EMA 20."
    return TechnicalSignalItem(
        indicator_name="ema_20",
        label="EMA 20",
        value={"ema": round(ema_val, 4), "close": round(close, 4), "diff_pct": round(diff_pct, 4)},
        signal=signal,
        score=score,
        strength=strength,
        reason=reason,
        timestamp=latest.timestamp,
    )


def _evaluate_rsi(rows: list[IndicatorValue]) -> TechnicalSignalItem:
    if not rows:
        return TechnicalSignalItem(
            indicator_name="rsi_14",
            label="RSI 14",
            value=None,
            signal="neutral",
            score=0,
            strength="weak",
            reason="No RSI 14 data available.",
            timestamp=None,
        )
    latest = rows[-1]
    raw = latest.values.get("value")
    if raw is None:
        return TechnicalSignalItem(
            indicator_name="rsi_14",
            label="RSI 14",
            value=None,
            signal="neutral",
            score=0,
            strength="weak",
            reason="No RSI 14 value available.",
            timestamp=latest.timestamp,
        )
    rsi_val = float(raw)
    if rsi_val < 30:
        signal, score, strength, reason = "buy", 1, "medium", "RSI is in oversold territory."
    elif rsi_val > 70:
        signal, score, strength, reason = "caution", -1, "medium", "RSI is in overbought territory."
    elif 55 < rsi_val <= 70:
        signal, score, strength, reason = (
            "buy",
            1,
            "weak",
            "RSI shows positive momentum but is not overbought.",
        )
    elif 30 <= rsi_val < 45:
        signal, score, strength, reason = "sell", -1, "weak", "RSI shows weak momentum."
    else:
        signal, score, strength, reason = "neutral", 0, "weak", "RSI is neutral."
    return TechnicalSignalItem(
        indicator_name="rsi_14",
        label="RSI 14",
        value=round(rsi_val, 2),
        signal=signal,
        score=score,
        strength=strength,
        reason=reason,
        timestamp=latest.timestamp,
    )


def _evaluate_macd(rows: list[IndicatorValue]) -> TechnicalSignalItem:
    if not rows:
        return TechnicalSignalItem(
            indicator_name="macd_12_26_9",
            label="MACD 12 26 9",
            value=None,
            signal="neutral",
            score=0,
            strength="weak",
            reason="No MACD data available.",
            timestamp=None,
        )
    latest = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    macd_raw = latest.values.get("macd")
    signal_raw = latest.values.get("signal")
    histogram_raw = latest.values.get("histogram")
    if macd_raw is None or signal_raw is None or histogram_raw is None:
        return TechnicalSignalItem(
            indicator_name="macd_12_26_9",
            label="MACD 12 26 9",
            value=None,
            signal="neutral",
            score=0,
            strength="weak",
            reason="Incomplete MACD data.",
            timestamp=latest.timestamp,
        )
    macd_val = float(macd_raw)
    signal_val = float(signal_raw)
    histogram_val = float(histogram_raw)
    value_dict: dict[str, Any] = {
        "macd": round(macd_val, 6),
        "signal": round(signal_val, 6),
        "histogram": round(histogram_val, 6),
    }
    # Histogram zero-cross takes priority over directional bias
    if prev is not None:
        prev_hist_raw = prev.values.get("histogram")
        if prev_hist_raw is not None:
            prev_hist = float(prev_hist_raw)
            if prev_hist < 0 and histogram_val > 0:
                return TechnicalSignalItem(
                    indicator_name="macd_12_26_9",
                    label="MACD 12 26 9",
                    value=value_dict,
                    signal="buy",
                    score=2,
                    strength="strong",
                    reason="MACD histogram crossed above zero.",
                    timestamp=latest.timestamp,
                )
            if prev_hist > 0 and histogram_val < 0:
                return TechnicalSignalItem(
                    indicator_name="macd_12_26_9",
                    label="MACD 12 26 9",
                    value=value_dict,
                    signal="sell",
                    score=-2,
                    strength="strong",
                    reason="MACD histogram crossed below zero.",
                    timestamp=latest.timestamp,
                )
    if macd_val > signal_val and histogram_val > 0:
        return TechnicalSignalItem(
            indicator_name="macd_12_26_9",
            label="MACD 12 26 9",
            value=value_dict,
            signal="buy",
            score=2,
            strength="medium",
            reason="MACD is above signal line with positive histogram.",
            timestamp=latest.timestamp,
        )
    if macd_val < signal_val and histogram_val < 0:
        return TechnicalSignalItem(
            indicator_name="macd_12_26_9",
            label="MACD 12 26 9",
            value=value_dict,
            signal="sell",
            score=-2,
            strength="medium",
            reason="MACD is below signal line with negative histogram.",
            timestamp=latest.timestamp,
        )
    return TechnicalSignalItem(
        indicator_name="macd_12_26_9",
        label="MACD 12 26 9",
        value=value_dict,
        signal="neutral",
        score=0,
        strength="weak",
        reason="MACD does not show a clear directional signal.",
        timestamp=latest.timestamp,
    )


def _evaluate_bollinger(close: float, rows: list[IndicatorValue]) -> TechnicalSignalItem:
    if not rows:
        return TechnicalSignalItem(
            indicator_name="bollinger_bands_20_2",
            label="Bollinger Bands 20 2",
            value=None,
            signal="neutral",
            score=0,
            strength="weak",
            reason="No Bollinger Bands data available.",
            timestamp=None,
        )
    latest = rows[-1]
    upper_raw = latest.values.get("upper")
    lower_raw = latest.values.get("lower")
    middle_raw = latest.values.get("middle")
    percent_b_raw = latest.values.get("percent_b")
    if upper_raw is None or lower_raw is None:
        return TechnicalSignalItem(
            indicator_name="bollinger_bands_20_2",
            label="Bollinger Bands 20 2",
            value=None,
            signal="neutral",
            score=0,
            strength="weak",
            reason="Incomplete Bollinger Bands data.",
            timestamp=latest.timestamp,
        )
    upper = float(upper_raw)
    lower = float(lower_raw)
    middle = float(middle_raw) if middle_raw is not None else None
    percent_b = float(percent_b_raw) if percent_b_raw is not None else None
    value_dict: dict[str, Any] = {
        "upper": round(upper, 4),
        "lower": round(lower, 4),
        "close": round(close, 4),
    }
    if middle is not None:
        value_dict["middle"] = round(middle, 4)
    if percent_b is not None:
        value_dict["percent_b"] = round(percent_b, 4)
    if close < lower:
        return TechnicalSignalItem(
            indicator_name="bollinger_bands_20_2",
            label="Bollinger Bands 20 2",
            value=value_dict,
            signal="buy",
            score=1,
            strength="medium",
            reason="Close is below the lower Bollinger Band.",
            timestamp=latest.timestamp,
        )
    if close > upper:
        return TechnicalSignalItem(
            indicator_name="bollinger_bands_20_2",
            label="Bollinger Bands 20 2",
            value=value_dict,
            signal="caution",
            score=-1,
            strength="medium",
            reason="Close is above the upper Bollinger Band.",
            timestamp=latest.timestamp,
        )
    if percent_b is not None and percent_b >= 0.9:
        return TechnicalSignalItem(
            indicator_name="bollinger_bands_20_2",
            label="Bollinger Bands 20 2",
            value=value_dict,
            signal="caution",
            score=-1,
            strength="weak",
            reason="Close is near the upper Bollinger Band.",
            timestamp=latest.timestamp,
        )
    if percent_b is not None and percent_b <= 0.1:
        return TechnicalSignalItem(
            indicator_name="bollinger_bands_20_2",
            label="Bollinger Bands 20 2",
            value=value_dict,
            signal="buy",
            score=1,
            strength="weak",
            reason="Close is near the lower Bollinger Band.",
            timestamp=latest.timestamp,
        )
    return TechnicalSignalItem(
        indicator_name="bollinger_bands_20_2",
        label="Bollinger Bands 20 2",
        value=value_dict,
        signal="neutral",
        score=0,
        strength="weak",
        reason="Close is inside the Bollinger Band range.",
        timestamp=latest.timestamp,
    )


def _evaluate_atr(close: float, rows: list[IndicatorValue]) -> TechnicalSignalItem:
    if not rows:
        return TechnicalSignalItem(
            indicator_name="atr_14",
            label="ATR 14",
            value=None,
            signal="info",
            score=0,
            strength="info",
            reason="No ATR 14 data available.",
            timestamp=None,
        )
    latest = rows[-1]
    raw = latest.values.get("value")
    if raw is None:
        return TechnicalSignalItem(
            indicator_name="atr_14",
            label="ATR 14",
            value=None,
            signal="info",
            score=0,
            strength="info",
            reason="No ATR 14 value available.",
            timestamp=latest.timestamp,
        )
    atr_val = float(raw)
    atr_percent = (atr_val / close * 100) if close != 0 else 0.0
    if atr_percent < 1:
        strength: str = "weak"
        reason = "Volatility is relatively low."
    elif atr_percent < 3:
        strength = "medium"
        reason = "Volatility is moderate."
    else:
        strength = "strong"
        reason = "Volatility is elevated."
    return TechnicalSignalItem(
        indicator_name="atr_14",
        label="ATR 14",
        value={"atr": round(atr_val, 4), "atr_percent": round(atr_percent, 4)},
        signal="info",
        score=0,
        strength=strength,  # type: ignore[arg-type]
        reason=reason,
        timestamp=latest.timestamp,
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate_signals(
    instrument_id: int,
    timeframe: str,
    signals: list[TechnicalSignalItem],
) -> TechnicalSignalAggregate:
    total_score = 0
    bullish_count = 0
    bearish_count = 0
    caution_count = 0
    info_count = 0
    actionable_count = 0

    for s in signals:
        if s.signal == "info":
            info_count += 1
            continue
        actionable_count += 1
        total_score += s.score
        if s.signal == "buy":
            bullish_count += 1
        elif s.signal == "sell":
            bearish_count += 1
        elif s.signal == "caution":
            caution_count += 1

    agg_signal: AggregateSignal
    if total_score >= 4:
        agg_signal = "strong_buy"
    elif total_score >= 2:
        agg_signal = "buy"
    elif total_score <= -4:
        agg_signal = "strong_sell"
    elif total_score <= -2:
        agg_signal = "sell"
    else:
        agg_signal = "neutral"

    # Caution override: multiple caution signals despite positive score
    if caution_count >= 2 and total_score > 0:
        agg_signal = "caution"

    confidence: Confidence
    if actionable_count >= 5:
        confidence = "high"
    elif actionable_count >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    return TechnicalSignalAggregate(
        instrument_id=instrument_id,
        timeframe=timeframe,
        total_score=total_score,
        signal=agg_signal,
        confidence=confidence,
        bullish_count=bullish_count,
        bearish_count=bearish_count,
        caution_count=caution_count,
        info_count=info_count,
        generated_at=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_technical_signals(
    db: Session,
    instrument_id: int,
    timeframe: str,
) -> TechnicalSignalResponse:
    candle = _load_latest_candle(db, instrument_id, timeframe)
    if candle is None:
        return TechnicalSignalResponse(
            instrument_id=instrument_id,
            timeframe=timeframe,
            aggregate=TechnicalSignalAggregate(
                instrument_id=instrument_id,
                timeframe=timeframe,
                total_score=0,
                signal="no_data",
                confidence="low",
                bullish_count=0,
                bearish_count=0,
                caution_count=0,
                info_count=0,
                generated_at=datetime.now(tz=timezone.utc),
            ),
            signals=[],
            message="No candles available for this instrument and timeframe.",
        )
    close = float(candle.close)

    sma_rows = _load_latest_indicator_rows(db, instrument_id, timeframe, "sma_20", limit=1)
    ema_rows = _load_latest_indicator_rows(db, instrument_id, timeframe, "ema_20", limit=1)
    rsi_rows = _load_latest_indicator_rows(db, instrument_id, timeframe, "rsi_14", limit=1)
    macd_rows = _load_latest_indicator_rows(db, instrument_id, timeframe, "macd_12_26_9", limit=2)
    bb_rows = _load_latest_indicator_rows(
        db, instrument_id, timeframe, "bollinger_bands_20_2", limit=1
    )
    atr_rows = _load_latest_indicator_rows(db, instrument_id, timeframe, "atr_14", limit=1)

    signals = [
        _evaluate_sma(close, sma_rows),
        _evaluate_ema(close, ema_rows),
        _evaluate_rsi(rsi_rows),
        _evaluate_macd(macd_rows),
        _evaluate_bollinger(close, bb_rows),
        _evaluate_atr(close, atr_rows),
    ]

    aggregate = _aggregate_signals(instrument_id, timeframe, signals)

    return TechnicalSignalResponse(
        instrument_id=instrument_id,
        timeframe=timeframe,
        aggregate=aggregate,
        signals=signals,
    )
