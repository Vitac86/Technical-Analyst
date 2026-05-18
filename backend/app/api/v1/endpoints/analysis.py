from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.analysis import TechnicalSignalResponse
from app.schemas.signal import AnalysisSignalRead
from app.services.analysis.signal_engine import generate_technical_signals


router = APIRouter()


@router.get("/signals", response_model=list[AnalysisSignalRead])
def list_signals() -> list[AnalysisSignalRead]:
    return []


@router.get("/technical-signals", response_model=TechnicalSignalResponse)
def get_technical_signals(
    instrument_id: int = Query(..., description="Instrument ID"),
    timeframe: str = Query(..., description="Timeframe (5m, 15m, 1h, 4h, 1d)"),
    db: Session = Depends(get_db),
) -> TechnicalSignalResponse:
    return generate_technical_signals(db, instrument_id=instrument_id, timeframe=timeframe)
