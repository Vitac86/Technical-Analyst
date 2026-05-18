from datetime import date
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.market_data import sync_service


router = APIRouter()


class SyncInstrumentRequest(BaseModel):
    ticker: str
    engine: str = "stock"
    market: str = "shares"
    board: str = "TQBR"


@router.post("/moex/instruments")
async def sync_moex_instruments(db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        return await sync_service.sync_moex_instruments(db)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"MOEX request failed: {exc}",
        ) from exc


@router.post("/moex/instrument")
async def sync_moex_instrument(
    request: SyncInstrumentRequest = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Upsert one instrument by its full MOEX source tuple (engine/market/board/ticker).

    Use this to load an instrument into the local database before syncing candles.
    """
    try:
        return await sync_service.sync_moex_instrument(
            db,
            ticker=request.ticker,
            engine=request.engine,
            market=request.market,
            board=request.board,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"MOEX request failed: {exc}",
        ) from exc


@router.post("/moex/candles")
async def sync_moex_candles(
    ticker: str = Query(..., min_length=1),
    timeframe: str = Query(default="1d", description="App timeframe: 5m,15m,1h,4h,1d"),
    start: date = Query(...),
    end: date = Query(...),
    engine: str | None = Query(default=None),
    market: str | None = Query(default=None),
    board: str | None = Query(default=None),
    calculate_indicators: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Sync candles for a ticker and optional app timeframe.

    Supports aggregated timeframes: 5m and 15m are fetched as 1m and aggregated;
    4h is fetched as 1h and aggregated.  1h and 1d are fetched directly.
    """
    try:
        candle_result = await sync_service.sync_moex_candles(
            db,
            ticker=ticker,
            timeframe=timeframe,
            start=start,
            end=end,
            engine=engine,
            market=market,
            board=board,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"MOEX request failed: {exc}",
        ) from exc

    if not calculate_indicators:
        return candle_result

    instrument_id = candle_result.get("instrument_id")
    if instrument_id is None:
        return candle_result

    from app.services.indicators.calculation_service import (
        calculate_default_indicators_for_instrument,
    )

    try:
        indicator_result = calculate_default_indicators_for_instrument(
            db,
            instrument_id=instrument_id,
            timeframe=timeframe,
        )
    except ValueError:
        indicator_result = None

    return {
        **candle_result,
        "indicator_sync": indicator_result,
    }
