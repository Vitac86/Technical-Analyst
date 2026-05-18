from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

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


def bulk_upsert_candles(
    db: Session,
    instrument_id: int,
    candles: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    """Insert or update candles by instrument, timeframe, and timestamp."""
    records = list(candles)
    summary = {
        "processed": len(records),
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
    }
    unique_values: dict[tuple[int, str, datetime], dict[str, Any]] = {}

    for candle_data in records:
        try:
            values = _candle_values(instrument_id, candle_data)
        except ValueError:
            summary["skipped"] += 1
            continue
        unique_values[
            (
                values["instrument_id"],
                values["timeframe"],
                values["timestamp"],
            )
        ] = values

    for values in unique_values.values():
        existing = _get_candle_for_upsert(
            db,
            instrument_id=values["instrument_id"],
            timeframe=values["timeframe"],
            timestamp=values["timestamp"],
        )

        if existing is None:
            db.add(Candle(**values))
            summary["inserted"] += 1
            continue

        changed = _apply_values(existing, values)
        if changed:
            summary["updated"] += 1
        else:
            summary["unchanged"] += 1

    db.flush()
    return summary


def _get_candle_for_upsert(
    db: Session,
    instrument_id: int,
    timeframe: str,
    timestamp: datetime,
) -> Candle | None:
    statement = select(Candle).where(
        Candle.instrument_id == instrument_id,
        Candle.timeframe == timeframe,
        Candle.timestamp == timestamp,
    )
    return db.execute(statement).scalar_one_or_none()


def _candle_values(
    instrument_id: int,
    candle_data: Mapping[str, Any],
) -> dict[str, Any]:
    timeframe = _clean_string(candle_data.get("timeframe"))
    timestamp = _coerce_datetime(candle_data.get("timestamp"))
    open_ = _decimal_required(candle_data.get("open"), "open")
    high = _decimal_required(candle_data.get("high"), "high")
    low = _decimal_required(candle_data.get("low"), "low")
    close = _decimal_required(candle_data.get("close"), "close")

    if timeframe is None:
        raise ValueError("Candle timeframe is required.")
    if timestamp is None:
        raise ValueError("Candle timestamp is required.")

    return {
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": _decimal_optional(candle_data.get("volume")),
    }


def _apply_values(candle: Candle, values: Mapping[str, Any]) -> bool:
    changed = False
    for key in ("open", "high", "low", "close", "volume"):
        value = values[key]
        if getattr(candle, key) != value:
            setattr(candle, key, value)
            changed = True
    return changed


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _decimal_required(value: Any, field_name: str) -> Decimal:
    converted = _decimal_optional(value)
    if converted is None:
        raise ValueError(f"Candle {field_name} is required.")
    return converted


def _decimal_optional(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value

    raw = str(value).strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned if cleaned else None
