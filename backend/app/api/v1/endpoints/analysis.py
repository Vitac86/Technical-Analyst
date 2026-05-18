from fastapi import APIRouter

from app.schemas.signal import AnalysisSignalRead
from app.services.analysis.signal_engine import SignalEngine


router = APIRouter()


@router.get("/signals", response_model=list[AnalysisSignalRead])
def list_signals() -> list[AnalysisSignalRead]:
    engine = SignalEngine()
    return engine.generate_signals()
