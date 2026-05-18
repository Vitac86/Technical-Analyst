from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import models as _models  # noqa: F401
from app.db.base import Base
from app.db.models.candle import Candle
from app.db.models.instrument import Instrument
from app.repositories import candles as candle_repository
from app.repositories import instruments as instrument_repository


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
    )

    with testing_session() as session:
        yield session

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_instrument_upsert_does_not_duplicate_tickers(db_session: Session) -> None:
    instrument_repository.upsert_instrument(
        db_session,
        {
            "ticker": "SBER",
            "name": "Sberbank",
            "market": "shares",
            "board": "TQBR",
            "currency": "RUB",
            "is_active": True,
        },
    )
    instrument_repository.upsert_instrument(
        db_session,
        {
            "ticker": "sber",
            "name": "Sberbank Updated",
            "market": "shares",
            "board": "TQBR",
            "currency": "RUB",
            "is_active": False,
        },
    )

    count = db_session.scalar(select(func.count()).select_from(Instrument))
    stored = instrument_repository.get_instrument_by_ticker(db_session, "SBER")

    assert count == 1
    assert stored is not None
    assert stored.name == "Sberbank Updated"
    assert stored.is_active is False


def test_candle_upsert_does_not_duplicate_candles(db_session: Session) -> None:
    instrument, _, _ = instrument_repository.upsert_instrument(
        db_session,
        {
            "ticker": "SBER",
            "name": "Sberbank",
            "market": "shares",
            "board": "TQBR",
            "currency": "RUB",
            "is_active": True,
        },
    )
    timestamp = datetime.fromisoformat("2024-01-03T00:00:00")

    candle_repository.bulk_upsert_candles(
        db_session,
        instrument_id=instrument.id,
        candles=[
            {
                "timeframe": "1d",
                "timestamp": timestamp,
                "open": Decimal("270"),
                "high": Decimal("275"),
                "low": Decimal("269"),
                "close": Decimal("274"),
                "volume": Decimal("1000"),
            }
        ],
    )
    candle_repository.bulk_upsert_candles(
        db_session,
        instrument_id=instrument.id,
        candles=[
            {
                "timeframe": "1d",
                "timestamp": timestamp,
                "open": Decimal("271"),
                "high": Decimal("276"),
                "low": Decimal("270"),
                "close": Decimal("275"),
                "volume": Decimal("1200"),
            }
        ],
    )

    count = db_session.scalar(select(func.count()).select_from(Candle))
    stored = candle_repository.list_candles(
        db_session,
        instrument_id=instrument.id,
        timeframe="1d",
    )

    assert count == 1
    assert len(stored) == 1
    assert stored[0].open == Decimal("271")
    assert stored[0].close == Decimal("275")
    assert stored[0].volume == Decimal("1200")
