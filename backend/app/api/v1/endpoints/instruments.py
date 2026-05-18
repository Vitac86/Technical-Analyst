from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories import instruments as instrument_repository
from app.schemas.instrument import InstrumentRead


router = APIRouter()


@router.get("", response_model=list[InstrumentRead])
def list_instruments(db: Session = Depends(get_db)) -> list[InstrumentRead]:
    return instrument_repository.list_instruments(db)
