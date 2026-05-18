from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.instrument import Instrument


def list_instruments(db: Session) -> list[Instrument]:
    result = db.execute(select(Instrument).order_by(Instrument.ticker))
    return list(result.scalars().all())


def get_instrument(db: Session, instrument_id: int) -> Instrument | None:
    return db.get(Instrument, instrument_id)


def get_instrument_by_ticker(db: Session, ticker: str) -> Instrument | None:
    statement = select(Instrument).where(Instrument.ticker == ticker.strip().upper())
    return db.execute(statement).scalar_one_or_none()


def get_instrument_by_source(
    db: Session,
    engine: str,
    market: str,
    board: str,
    ticker: str,
) -> Instrument | None:
    """Look up instrument by the full MOEX source tuple (engine, market, board, ticker).

    Falls back to ticker-only lookup so existing instruments without engine/market/board
    populated are still found.
    """
    normalized = ticker.strip().upper()
    statement = select(Instrument).where(
        Instrument.ticker == normalized,
        Instrument.engine == engine,
        Instrument.market == market,
        Instrument.board == board,
    )
    result = db.execute(statement).scalar_one_or_none()
    if result is not None:
        return result
    # Fallback: ticker only (handles legacy rows that lack engine/market/board)
    return get_instrument_by_ticker(db, normalized)


def upsert_instrument(
    db: Session,
    instrument_data: Mapping[str, Any],
) -> tuple[Instrument, bool, bool]:
    """Insert or update one instrument by ticker.

    Returns the ORM object plus booleans for ``created`` and ``changed``.
    """
    values = _instrument_values(instrument_data)
    existing = get_instrument_by_ticker(db, values["ticker"])

    if existing is None:
        instrument = Instrument(**values)
        db.add(instrument)
        db.flush()
        return instrument, True, True

    changed = _apply_values(existing, values)
    if changed:
        db.flush()
    return existing, False, changed


def upsert_instruments(
    db: Session,
    instruments: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    records = list(instruments)
    summary = {
        "processed": len(records),
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
    }

    for instrument_data in records:
        try:
            _, created, changed = upsert_instrument(db, instrument_data)
        except ValueError:
            summary["skipped"] += 1
            continue

        if created:
            summary["inserted"] += 1
        elif changed:
            summary["updated"] += 1
        else:
            summary["unchanged"] += 1

    return summary


def _instrument_values(instrument_data: Mapping[str, Any]) -> dict[str, Any]:
    ticker = _clean_upper(instrument_data.get("ticker"))
    if ticker is None:
        raise ValueError("Instrument ticker is required.")

    return {
        "ticker": ticker,
        "name": _clean_string(instrument_data.get("name")) or ticker,
        "engine": _clean_string(instrument_data.get("engine")),
        "market": _clean_string(instrument_data.get("market")),
        "board": _clean_string(instrument_data.get("board")),
        "currency": _clean_string(instrument_data.get("currency")),
        "is_active": _coerce_bool(instrument_data.get("is_active", True)),
    }


def _apply_values(instrument: Instrument, values: Mapping[str, Any]) -> bool:
    changed = False
    for key, value in values.items():
        if getattr(instrument, key) != value:
            setattr(instrument, key, value)
            changed = True
    return changed


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned if cleaned else None


def _clean_upper(value: Any) -> str | None:
    cleaned = _clean_string(value)
    return cleaned.upper() if cleaned is not None else None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in {"", "0", "false", "no", "n", "inactive"}
