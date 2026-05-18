from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db.models.candle import Candle


def list_candles(
    db: Session,
    instrument_id: int,
    timeframe: str | None = None,
) -> list[Candle]:
    statement: Select[tuple[Candle]] = select(Candle).where(
        Candle.instrument_id == instrument_id,
    )
    if timeframe is not None:
        statement = statement.where(Candle.timeframe == timeframe)
    statement = statement.order_by(Candle.timestamp)
    result = db.execute(statement)
    return list(result.scalars().all())
