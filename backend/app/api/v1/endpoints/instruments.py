from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories import instruments as instrument_repository
from app.schemas.instrument import InstrumentRead
from app.services.market_data.moex_provider import MoexProvider


router = APIRouter()


@router.get("", response_model=list[InstrumentRead])
def list_instruments(db: Session = Depends(get_db)) -> list[InstrumentRead]:
    return instrument_repository.list_instruments(db)


@router.get("/search")
async def search_instruments(
    query: str = Query(..., min_length=1, description="Ticker or name to search"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Search MOEX for instruments without requiring a full market sync.

    Returns candidate instruments with ticker, name, engine, market, board,
    is_active, and group.  Currency is not available at search time.
    """
    try:
        provider = MoexProvider()
        return await provider.find_instruments(query.strip(), limit=limit)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"MOEX search request failed: {exc}",
        ) from exc


@router.get("/{instrument_id}/summary")
def instrument_summary(
    instrument_id: int,
    timeframe: str = Query(default="1d"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return last-price summary computed from stored candles (no real-time quotes)."""
    instrument = instrument_repository.get_instrument(db, instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="Instrument not found.")

    from app.repositories import candles as candle_repository

    candles = candle_repository.list_candles(db, instrument_id=instrument_id, timeframe=timeframe)
    return {
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        **_last_price_summary(candles),
    }


def _last_price_summary(candles: list) -> dict[str, Any]:
    if not candles:
        return {
            "last_close": None,
            "previous_close": None,
            "change": None,
            "change_percent": None,
            "last_timestamp": None,
        }
    last = candles[-1]
    prev = candles[-2] if len(candles) > 1 else None
    last_close = float(last.close)
    prev_close = float(prev.close) if prev else None
    change = round(last_close - prev_close, 6) if prev_close is not None else None
    change_pct = (
        round(change / prev_close * 100, 4)
        if change is not None and prev_close
        else None
    )
    return {
        "last_close": last_close,
        "previous_close": prev_close,
        "change": change,
        "change_percent": change_pct,
        "last_timestamp": last.timestamp.isoformat() if last.timestamp else None,
    }
