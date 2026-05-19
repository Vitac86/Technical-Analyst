from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.candle import Candle
from app.repositories.instruments import get_instrument_by_source
from app.schemas.scanner import (
    ScannerInstrumentRequest,
    ScannerResponse,
    ScannerRow,
)
from app.services.analysis.levels_engine import generate_technical_levels
from app.services.analysis.signal_engine import generate_technical_signals


def _candle_count(db: Session, instrument_id: int, timeframe: str) -> int:
    stmt = select(func.count()).where(
        Candle.instrument_id == instrument_id,
        Candle.timeframe == timeframe,
    )
    return db.execute(stmt).scalar_one()


def _extract_rsi(signals: list) -> float | None:
    for s in signals:
        if s.indicator_name == "rsi_14" and s.value is not None:
            raw = s.value
            if isinstance(raw, (int, float)):
                return float(raw)
    return None


def _extract_macd_histogram(signals: list) -> float | None:
    for s in signals:
        if s.indicator_name == "macd_12_26_9" and isinstance(s.value, dict):
            raw = s.value.get("histogram")
            if raw is not None:
                return float(raw)
    return None


def _extract_atr_percent(signals: list) -> float | None:
    for s in signals:
        if s.indicator_name == "atr_14" and isinstance(s.value, dict):
            raw = s.value.get("atr_percent")
            if raw is not None:
                return float(raw)
    return None


def _scan_one(
    db: Session,
    req: ScannerInstrumentRequest,
    timeframe: str,
    lookback: int,
) -> ScannerRow:
    ticker = req.ticker.strip().upper()

    instrument = get_instrument_by_source(
        db,
        engine=req.engine,
        market=req.market,
        board=req.board,
        ticker=ticker,
    )
    if instrument is None:
        return ScannerRow(
            ticker=ticker,
            timeframe=timeframe,
            status="no_instrument",
        )

    count = _candle_count(db, instrument.id, timeframe)
    if count == 0:
        return ScannerRow(
            ticker=ticker,
            name=instrument.name,
            engine=instrument.engine,
            market=instrument.market,
            board=instrument.board,
            instrument_id=instrument.id,
            timeframe=timeframe,
            status="no_candles",
        )

    sig_resp = generate_technical_signals(db, instrument_id=instrument.id, timeframe=timeframe)
    lvl_resp = generate_technical_levels(
        db, instrument_id=instrument.id, timeframe=timeframe, lookback=lookback
    )

    agg = sig_resp.aggregate

    if agg.signal == "no_data":
        return ScannerRow(
            ticker=ticker,
            name=instrument.name,
            engine=instrument.engine,
            market=instrument.market,
            board=instrument.board,
            instrument_id=instrument.id,
            timeframe=timeframe,
            status="no_indicators",
            last_close=lvl_resp.last_close,
        )

    rsi = _extract_rsi(sig_resp.signals)
    macd_hist = _extract_macd_histogram(sig_resp.signals)
    atr_pct = _extract_atr_percent(sig_resp.signals)
    if atr_pct is None:
        atr_pct = lvl_resp.atr_percent

    nearest_support: float | None = None
    nearest_resistance: float | None = None
    dist_support: float | None = None
    dist_resistance: float | None = None

    for level in lvl_resp.levels:
        if level.kind == "support" and level.label == "Nearest Support":
            nearest_support = level.price
            dist_support = level.distance_percent
        elif level.kind == "resistance" and level.label == "Nearest Resistance":
            nearest_resistance = level.price
            dist_resistance = level.distance_percent

    last_close = lvl_resp.last_close

    return ScannerRow(
        ticker=ticker,
        name=instrument.name,
        engine=instrument.engine,
        market=instrument.market,
        board=instrument.board,
        instrument_id=instrument.id,
        timeframe=timeframe,
        status="ok",
        last_close=last_close,
        aggregate_signal=agg.signal,
        total_score=agg.total_score,
        confidence=agg.confidence,
        bullish_count=agg.bullish_count,
        bearish_count=agg.bearish_count,
        caution_count=agg.caution_count,
        rsi=rsi,
        macd_histogram=macd_hist,
        atr_percent=atr_pct,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        distance_to_support_percent=dist_support,
        distance_to_resistance_percent=dist_resistance,
        summary=lvl_resp.summary if lvl_resp.summary != "No data available." else None,
    )


def scan_watchlist(
    db: Session,
    instruments: list[ScannerInstrumentRequest],
    timeframe: str,
    lookback: int = 100,
) -> ScannerResponse:
    rows: list[ScannerRow] = []

    for req in instruments:
        try:
            row = _scan_one(db, req, timeframe, lookback)
        except Exception as exc:
            rows.append(
                ScannerRow(
                    ticker=req.ticker.strip().upper(),
                    timeframe=timeframe,
                    status="error",
                    error=str(exc),
                )
            )
            continue
        rows.append(row)

    def _sort_key(r: ScannerRow) -> tuple:
        status_order = {"ok": 0, "no_indicators": 1, "no_candles": 2, "no_instrument": 3, "error": 4}
        score = -(r.total_score or 0)
        return (status_order.get(r.status, 9), score, r.ticker)

    rows.sort(key=_sort_key)

    return ScannerResponse(
        timeframe=timeframe,
        rows=rows,
        generated_at=datetime.now(tz=timezone.utc),
    )
