from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories import candles as candle_repository
from app.schemas.candle import CandleRead


router = APIRouter()


@router.get("", response_model=list[CandleRead])
def list_candles(
    instrument_id: int | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[CandleRead]:
    if instrument_id is None:
        return []
    return candle_repository.list_candles(
        db,
        instrument_id=instrument_id,
        timeframe=timeframe,
    )
