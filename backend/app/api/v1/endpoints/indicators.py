from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories import indicators as indicator_repository
from app.schemas.indicator import IndicatorCalculationRequest, IndicatorValueRead
from app.services.indicators.calculation_service import (
    calculate_default_indicators_for_instrument,
    calculate_indicator_for_instrument,
)


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


@router.post("/calculate")
def calculate_indicator(
    request: IndicatorCalculationRequest | None = Body(default=None),
    instrument_id: int | None = Query(default=None),
    timeframe: str = Query(default="1d"),
    indicator_name: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    resolved_instrument_id = (
        request.instrument_id if request is not None else instrument_id
    )
    resolved_timeframe = request.timeframe if request is not None else timeframe
    resolved_indicator_name = (
        request.indicator_name if request is not None else indicator_name
    )
    resolved_params = request.params if request is not None else None

    if resolved_instrument_id is None:
        raise HTTPException(status_code=400, detail="instrument_id is required.")
    if resolved_indicator_name is None:
        raise HTTPException(status_code=400, detail="indicator_name is required.")

    try:
        return calculate_indicator_for_instrument(
            db,
            instrument_id=resolved_instrument_id,
            timeframe=resolved_timeframe,
            indicator_name=resolved_indicator_name,
            params=resolved_params,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/calculate-defaults")
def calculate_default_indicators(
    instrument_id: int = Query(...),
    timeframe: str = Query(default="1d"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return calculate_default_indicators_for_instrument(
            db,
            instrument_id=instrument_id,
            timeframe=timeframe,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
