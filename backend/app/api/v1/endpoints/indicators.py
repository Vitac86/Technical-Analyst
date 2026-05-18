from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories import indicators as indicator_repository
from app.schemas.indicator import IndicatorValueRead


router = APIRouter()


@router.get("", response_model=list[IndicatorValueRead])
def list_indicator_values(
    instrument_id: int | None = Query(default=None),
    indicator_name: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[IndicatorValueRead]:
    if instrument_id is None:
        return []
    return indicator_repository.list_indicator_values(
        db,
        instrument_id=instrument_id,
        indicator_name=indicator_name,
    )
