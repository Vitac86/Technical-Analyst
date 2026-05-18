from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import candles as candle_repository
from app.repositories import instruments as instrument_repository
from app.services.market_data.moex_provider import (
    MOEX_MARKET,
    MOEX_SHARES_BOARD,
    MoexProvider,
    timeframe_to_interval,
)


async def sync_moex_instruments(
    db: Session,
    provider: MoexProvider | None = None,
) -> dict[str, Any]:
    """Fetch MOEX share instruments and persist them in one transaction."""
    provider = provider or MoexProvider()

    try:
        instruments = await provider.fetch_instruments()
        repository_summary = instrument_repository.upsert_instruments(db, instruments)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "provider": provider.name,
        "entity": "instruments",
        **repository_summary,
    }


async def sync_moex_candles(
    db: Session,
    ticker: str,
    timeframe: str,
    start: date | datetime,
    end: date | datetime,
    provider: MoexProvider | None = None,
) -> dict[str, Any]:
    """Fetch MOEX candles for one ticker and persist them in one transaction."""
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("Ticker must not be empty.")
    timeframe_to_interval(timeframe)
    if _date_only(start) > _date_only(end):
        raise ValueError("Start date must be before or equal to end date.")

    provider = provider or MoexProvider()
    instrument_summary: dict[str, Any] | None = None

    try:
        instrument = instrument_repository.get_instrument_by_ticker(
            db,
            normalized_ticker,
        )

        if instrument is None:
            fetched_instruments = await provider.fetch_instruments()
            instrument_summary = instrument_repository.upsert_instruments(
                db,
                fetched_instruments,
            )
            instrument = instrument_repository.get_instrument_by_ticker(
                db,
                normalized_ticker,
            )

        candles = await provider.fetch_candles(
            normalized_ticker,
            timeframe,
            start,
            end,
        )

        if instrument is None and candles:
            instrument, created, changed = instrument_repository.upsert_instrument(
                db,
                {
                    "ticker": normalized_ticker,
                    "name": normalized_ticker,
                    "market": MOEX_MARKET,
                    "board": MOEX_SHARES_BOARD,
                    "currency": "RUB",
                    "is_active": True,
                },
            )
            instrument_summary = {
                "processed": 1,
                "inserted": int(created),
                "updated": int((not created) and changed),
                "unchanged": int((not created) and (not changed)),
                "skipped": 0,
                "fallback_created_from_ticker": 1,
            }

        if instrument is None:
            candle_summary = {
                "processed": len(candles),
                "inserted": 0,
                "updated": 0,
                "unchanged": 0,
                "skipped": 0,
            }
            instrument_id = None
        else:
            candle_summary = candle_repository.bulk_upsert_candles(
                db,
                instrument_id=instrument.id,
                candles=candles,
            )
            instrument_id = instrument.id

        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "provider": provider.name,
        "entity": "candles",
        "ticker": normalized_ticker,
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        "start": _date_only(start).isoformat(),
        "end": _date_only(end).isoformat(),
        "instrument_sync": instrument_summary,
        **candle_summary,
    }


def _date_only(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value
