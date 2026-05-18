"""Workspace load endpoint: sync candles + indicators in one call."""

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories import candles as candle_repository
from app.repositories import instruments as instrument_repository
from app.schemas.workspace import LastPriceSummary, WorkspaceLoadRequest, WorkspaceLoadResponse
from app.services.indicators.calculation_service import (
    calculate_default_indicators_for_instrument,
)
from app.services.market_data import sync_service


router = APIRouter()


@router.post("", response_model=WorkspaceLoadResponse)
async def workspace_load(
    request: WorkspaceLoadRequest,
    db: Session = Depends(get_db),
) -> WorkspaceLoadResponse:
    """Sync candles and recalculate indicators for the selected instrument/timeframe.

    One call covers: instrument upsert → candle sync → indicator calculation →
    last-price summary.  Returns everything needed for the chart to update.
    """
    try:
        candle_sync = await sync_service.sync_moex_candles(
            db,
            ticker=request.ticker,
            timeframe=request.timeframe,
            start=request.start,
            end=request.end,
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

    instrument_id: int | None = candle_sync.get("instrument_id")
    if instrument_id is None:
        raise HTTPException(
            status_code=500,
            detail="Instrument could not be created or located.",
        )

    instrument = instrument_repository.get_instrument(db, instrument_id)
    if instrument is None:
        raise HTTPException(status_code=500, detail="Instrument vanished after sync.")

    # Calculate default indicators if requested.
    indicator_sync: dict[str, Any] | None = None
    if request.calculate_indicators:
        try:
            indicator_sync = calculate_default_indicators_for_instrument(
                db,
                instrument_id=instrument_id,
                timeframe=request.timeframe,
            )
        except ValueError:
            # Not enough candles yet — return without indicators.
            indicator_sync = None

    # Build last-price summary from stored candles.
    candles = candle_repository.list_candles(
        db, instrument_id=instrument_id, timeframe=request.timeframe
    )
    last_price = _build_last_price(candles)

    return WorkspaceLoadResponse(
        instrument=instrument,  # type: ignore[arg-type]
        candle_sync=candle_sync,
        indicator_sync=indicator_sync,
        last_price=last_price,
    )


def _build_last_price(candles: list) -> LastPriceSummary:
    if not candles:
        return LastPriceSummary()

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
    return LastPriceSummary(
        last_close=last_close,
        previous_close=prev_close,
        change=change,
        change_percent=change_pct,
        last_timestamp=last.timestamp,
    )
