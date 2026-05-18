from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import candles as candle_repository
from app.repositories import instruments as instrument_repository
from app.services.market_data.moex_provider import (
    MOEX_ENGINE,
    MOEX_MARKET,
    MOEX_SHARES_BOARD,
    MoexInstrumentSource,
    MoexProvider,
)
from app.services.market_data.timeframe_service import (
    aggregate_candles,
    get_moex_fetch_timeframe,
    needs_aggregation,
    validate_app_timeframe,
)


async def sync_moex_instruments(
    db: Session,
    provider: MoexProvider | None = None,
) -> dict[str, Any]:
    """Fetch MOEX TQBR share instruments and persist them in one transaction."""
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


async def sync_moex_instrument(
    db: Session,
    ticker: str,
    engine: str,
    market: str,
    board: str,
    provider: MoexProvider | None = None,
) -> dict[str, Any]:
    """Upsert one instrument by its full MOEX source tuple."""
    provider = provider or MoexProvider()
    source = MoexInstrumentSource(
        engine=engine,
        market=market,
        board=board,
        ticker=ticker.strip().upper(),
    )

    try:
        instrument_data = await provider.fetch_instrument(source)
        if instrument_data is None:
            instrument_data = {
                "ticker": source.ticker,
                "name": source.ticker,
                "engine": engine,
                "market": market,
                "board": board,
                "currency": None,
                "is_active": True,
            }

        instrument, created, changed = instrument_repository.upsert_instrument(
            db, instrument_data
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "provider": provider.name,
        "entity": "instrument",
        "ticker": source.ticker,
        "instrument_id": instrument.id,
        "created": created,
        "changed": changed,
    }


async def sync_moex_candles(
    db: Session,
    ticker: str,
    timeframe: str,
    start: date | datetime,
    end: date | datetime,
    engine: str | None = None,
    market: str | None = None,
    board: str | None = None,
    provider: MoexProvider | None = None,
) -> dict[str, Any]:
    """Fetch, optionally aggregate, and persist candles for one instrument.

    ``timeframe`` is an *app* timeframe (5m, 15m, 1h, 4h, 1d).  The function
    maps it to the appropriate MOEX ISS interval, fetches raw candles, runs
    aggregation if needed, and stores the result under the app timeframe label.

    If ``engine``/``market``/``board`` are provided the instrument is looked up
    (or created) by the full source tuple.  If not provided the function falls
    back to a ticker-only lookup; if the stored instrument has engine/market/board
    those are used, otherwise defaults to stock/shares/TQBR.
    """
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("Ticker must not be empty.")
    validate_app_timeframe(timeframe)
    if _date_only(start) > _date_only(end):
        raise ValueError("Start date must be before or equal to end date.")

    provider = provider or MoexProvider()

    # --- Resolve source tuple ---
    resolved_engine = engine or MOEX_ENGINE
    resolved_market = market or MOEX_MARKET
    resolved_board = board or MOEX_SHARES_BOARD
    instrument_summary: dict[str, Any] | None = None

    try:
        # Try to find instrument by full source tuple first.
        instrument = instrument_repository.get_instrument_by_source(
            db,
            engine=resolved_engine,
            market=resolved_market,
            board=resolved_board,
            ticker=normalized_ticker,
        )

        if instrument is not None and engine is None:
            # Use the engine/market/board stored on the existing instrument.
            resolved_engine = instrument.engine or resolved_engine
            resolved_market = instrument.market or resolved_market
            resolved_board = instrument.board or resolved_board

        if instrument is None:
            # Try to fetch instrument metadata from MOEX.
            source_for_fetch = MoexInstrumentSource(
                engine=resolved_engine,
                market=resolved_market,
                board=resolved_board,
                ticker=normalized_ticker,
            )
            fetched = await provider.fetch_instrument(source_for_fetch)
            if fetched is None:
                fetched = {
                    "ticker": normalized_ticker,
                    "name": normalized_ticker,
                    "engine": resolved_engine,
                    "market": resolved_market,
                    "board": resolved_board,
                    "currency": None,
                    "is_active": True,
                }
            instrument, created, changed = instrument_repository.upsert_instrument(
                db, fetched
            )
            instrument_summary = {
                "processed": 1,
                "inserted": int(created),
                "updated": int((not created) and changed),
                "unchanged": int((not created) and (not changed)),
                "skipped": 0,
            }

        # --- Fetch raw candles from MOEX ---
        moex_timeframe = get_moex_fetch_timeframe(timeframe)
        source = MoexInstrumentSource(
            engine=resolved_engine,
            market=resolved_market,
            board=resolved_board,
            ticker=normalized_ticker,
        )
        raw_candles = await provider.fetch_candles_by_source(
            source, moex_timeframe, start, end
        )

        # --- Aggregate if required ---
        if needs_aggregation(timeframe):
            final_candles = aggregate_candles(raw_candles, timeframe)
        else:
            final_candles = [dict(c, timeframe=timeframe) for c in raw_candles]

        # --- Persist ---
        candle_summary = candle_repository.bulk_upsert_candles(
            db,
            instrument_id=instrument.id,
            candles=final_candles,
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
        "moex_fetch_timeframe": moex_timeframe,
        "start": _date_only(start).isoformat(),
        "end": _date_only(end).isoformat(),
        "engine": resolved_engine,
        "market": resolved_market,
        "board": resolved_board,
        "instrument_sync": instrument_summary,
        **candle_summary,
    }


def _date_only(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value
