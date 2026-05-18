from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db.models.indicator_value import IndicatorValue


def list_indicator_values(
    db: Session,
    instrument_id: int,
    indicator_name: str | None = None,
    timeframe: str | None = None,
) -> list[IndicatorValue]:
    statement: Select[tuple[IndicatorValue]] = select(IndicatorValue).where(
        IndicatorValue.instrument_id == instrument_id,
    )
    if indicator_name is not None:
        statement = statement.where(IndicatorValue.indicator_name == indicator_name)
    if timeframe is not None:
        statement = statement.where(IndicatorValue.timeframe == timeframe)
    statement = statement.order_by(IndicatorValue.timestamp)
    result = db.execute(statement)
    return list(result.scalars().all())


def bulk_upsert_indicator_values(
    db: Session,
    *,
    instrument_id: int,
    indicator_name: str,
    category: str,
    timeframe: str,
    timestamps: pd.Series,
    values: pd.Series | pd.DataFrame,
) -> dict[str, int]:
    """Insert or update indicator values by instrument, name, timeframe, timestamp."""
    records, processed, skipped = _indicator_records(
        instrument_id=instrument_id,
        indicator_name=indicator_name,
        category=category,
        timeframe=timeframe,
        timestamps=timestamps,
        values=values,
    )
    summary = {
        "processed": processed,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": skipped,
    }

    unique_values: dict[tuple[int, str, str, datetime], dict[str, Any]] = {}
    for record in records:
        unique_values[
            (
                record["instrument_id"],
                record["indicator_name"],
                record["timeframe"],
                record["timestamp"],
            )
        ] = record

    for record in unique_values.values():
        existing = _get_indicator_value_for_upsert(
            db,
            instrument_id=record["instrument_id"],
            indicator_name=record["indicator_name"],
            timeframe=record["timeframe"],
            timestamp=record["timestamp"],
        )

        if existing is None:
            db.add(IndicatorValue(**record))
            summary["inserted"] += 1
            continue

        changed = _apply_indicator_values(existing, record)
        if changed:
            summary["updated"] += 1
        else:
            summary["unchanged"] += 1

    db.flush()
    return summary


def _indicator_records(
    *,
    instrument_id: int,
    indicator_name: str,
    category: str,
    timeframe: str,
    timestamps: pd.Series,
    values: pd.Series | pd.DataFrame,
) -> tuple[list[dict[str, Any]], int, int]:
    value_frame = (
        values.to_frame(name="value") if isinstance(values, pd.Series) else values.copy()
    )
    timestamp_series = timestamps.reindex(value_frame.index)
    records: list[dict[str, Any]] = []
    skipped = 0

    for index, row in value_frame.iterrows():
        timestamp = _coerce_timestamp(timestamp_series.loc[index])
        cleaned_values = _clean_indicator_values(row.to_dict())

        if timestamp is None or not cleaned_values:
            skipped += 1
            continue

        records.append(
            {
                "instrument_id": instrument_id,
                "indicator_name": indicator_name,
                "category": category,
                "timeframe": timeframe,
                "timestamp": timestamp,
                "values": cleaned_values,
            }
        )

    return records, len(value_frame), skipped


def _get_indicator_value_for_upsert(
    db: Session,
    *,
    instrument_id: int,
    indicator_name: str,
    timeframe: str,
    timestamp: datetime,
) -> IndicatorValue | None:
    statement = select(IndicatorValue).where(
        IndicatorValue.instrument_id == instrument_id,
        IndicatorValue.indicator_name == indicator_name,
        IndicatorValue.timeframe == timeframe,
        IndicatorValue.timestamp == timestamp,
    )
    return db.execute(statement).scalar_one_or_none()


def _apply_indicator_values(
    indicator_value: IndicatorValue,
    values: Mapping[str, Any],
) -> bool:
    changed = False
    for key in ("category", "values"):
        value = values[key]
        if getattr(indicator_value, key) != value:
            setattr(indicator_value, key, value)
            changed = True
    return changed


def _clean_indicator_values(values: Mapping[str, Any]) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for key, value in values.items():
        if pd.isna(value):
            continue
        cleaned[str(key)] = float(value)
    return cleaned


def _coerce_timestamp(value: Any) -> datetime | None:
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value

    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return None
    return timestamp.to_pydatetime()
