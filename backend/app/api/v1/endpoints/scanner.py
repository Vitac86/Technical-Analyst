from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.scanner import ScannerRequest, ScannerResponse
from app.services.analysis.scanner_engine import scan_watchlist

router = APIRouter()


@router.post("", response_model=ScannerResponse)
def run_scanner(
    body: ScannerRequest,
    db: Session = Depends(get_db),
) -> ScannerResponse:
    return scan_watchlist(
        db,
        instruments=body.instruments,
        timeframe=body.timeframe,
        lookback=body.lookback,
    )
