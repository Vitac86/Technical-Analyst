from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db.models.indicator_value import IndicatorValue


def list_indicator_values(
    db: Session,
    instrument_id: int,
    indicator_name: str | None = None,
) -> list[IndicatorValue]:
    statement: Select[tuple[IndicatorValue]] = select(IndicatorValue).where(
        IndicatorValue.instrument_id == instrument_id,
    )
    if indicator_name is not None:
        statement = statement.where(IndicatorValue.indicator_name == indicator_name)
    statement = statement.order_by(IndicatorValue.timestamp)
    result = db.execute(statement)
    return list(result.scalars().all())
