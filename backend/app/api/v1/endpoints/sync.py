from datetime import date

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.market_data import sync_service


router = APIRouter()


@router.post("/moex/instruments")
async def sync_moex_instruments(db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        return await sync_service.sync_moex_instruments(db)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"MOEX request failed: {exc}",
        ) from exc


@router.post("/moex/candles")
async def sync_moex_candles(
    ticker: str = Query(..., min_length=1),
    timeframe: str = Query(default="1d"),
    start: date = Query(...),
    end: date = Query(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        return await sync_service.sync_moex_candles(
            db,
            ticker=ticker,
            timeframe=timeframe,
            start=start,
            end=end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"MOEX request failed: {exc}",
        ) from exc
