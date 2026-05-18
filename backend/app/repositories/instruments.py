from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.instrument import Instrument


def list_instruments(db: Session) -> list[Instrument]:
    result = db.execute(select(Instrument).order_by(Instrument.ticker))
    return list(result.scalars().all())


def get_instrument(db: Session, instrument_id: int) -> Instrument | None:
    return db.get(Instrument, instrument_id)
