from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import models as _models  # noqa: F401
from app.db.base import Base
from app.db.models.indicator_value import IndicatorValue
from app.db.models.instrument import Instrument
from app.repositories import candles as candle_repository
from app.repositories import indicators as indicator_repository
from app.services.indicators.calculation_service import (
    calculate_default_indicators_for_instrument,
)
from app.services.indicators.momentum import rsi
from app.services.indicators.trend import ema, macd, sma
from app.services.indicators.volatility import atr, bollinger_bands


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


def test_sma_calculation_on_known_close_prices() -> None:
    result = sma(_candle_frame([1, 2, 3, 4, 5]), window=3)

    assert result.name == "sma"
    assert result.iloc[:2].isna().all()
    assert result.iloc[2:].tolist() == pytest.approx([2.0, 3.0, 4.0])


def test_ema_returns_expected_length_and_no_fake_early_values() -> None:
    result = ema(_candle_frame([1, 2, 3, 4, 5]), window=3)

    assert len(result) == 5
    assert result.iloc[:2].isna().all()
    assert result.iloc[2:].notna().all()


def test_rsi_values_stay_between_zero_and_one_hundred_after_warmup() -> None:
    result = rsi(_candle_frame([100 + index for index in range(30)]), window=14)

    valid_values = result.dropna()
    assert not valid_values.empty
    assert valid_values.between(0, 100).all()


def test_macd_returns_expected_columns() -> None:
    result = macd(_candle_frame([100 + index for index in range(60)]))

    assert list(result.columns) == ["macd", "signal", "histogram"]
    assert len(result) == 60


def test_bollinger_bands_return_ordered_bands_after_warmup() -> None:
    result = bollinger_bands(_candle_frame([100 + index for index in range(30)]))

    assert {"upper", "middle", "lower"}.issubset(result.columns)
    valid_values = result.dropna(subset=["upper", "middle", "lower"])
    assert not valid_values.empty
    assert (valid_values["upper"] >= valid_values["middle"]).all()
    assert (valid_values["middle"] >= valid_values["lower"]).all()


def test_atr_returns_positive_values_after_warmup() -> None:
    result = atr(_candle_frame([100 + index for index in range(30)]), window=14)

    valid_values = result.dropna()
    assert not valid_values.empty
    assert (valid_values > 0).all()


def test_indicator_persistence_skips_nan_only_rows(db_session: Session) -> None:
    timestamps = pd.Series(pd.date_range("2024-01-01", periods=3, freq="D"))
    values = pd.Series([pd.NA, float("nan"), 10.0])

    summary = indicator_repository.bulk_upsert_indicator_values(
        db_session,
        instrument_id=1,
        indicator_name="sma_3",
        category="trend",
        timeframe="1d",
        timestamps=timestamps,
        values=values,
    )

    stored = indicator_repository.list_indicator_values(
        db_session,
        instrument_id=1,
        indicator_name="sma_3",
    )

    assert summary["processed"] == 3
    assert summary["skipped"] == 2
    assert len(stored) == 1
    assert stored[0].values == {"value": 10.0}


def test_indicator_upsert_does_not_duplicate_values(db_session: Session) -> None:
    timestamps = pd.Series([datetime.fromisoformat("2024-01-01T00:00:00")])

    indicator_repository.bulk_upsert_indicator_values(
        db_session,
        instrument_id=1,
        indicator_name="rsi_14",
        category="momentum",
        timeframe="1d",
        timestamps=timestamps,
        values=pd.Series([55.0]),
    )
    unchanged_summary = indicator_repository.bulk_upsert_indicator_values(
        db_session,
        instrument_id=1,
        indicator_name="rsi_14",
        category="momentum",
        timeframe="1d",
        timestamps=timestamps,
        values=pd.Series([55.0]),
    )
    updated_summary = indicator_repository.bulk_upsert_indicator_values(
        db_session,
        instrument_id=1,
        indicator_name="rsi_14",
        category="momentum",
        timeframe="1d",
        timestamps=timestamps,
        values=pd.Series([57.0]),
    )

    count = db_session.scalar(select(func.count()).select_from(IndicatorValue))
    stored = indicator_repository.list_indicator_values(
        db_session,
        instrument_id=1,
        indicator_name="rsi_14",
    )

    assert unchanged_summary["unchanged"] == 1
    assert updated_summary["updated"] == 1
    assert count == 1
    assert stored[0].values == {"value": 57.0}


def test_calculate_default_indicators_persists_expected_names(
    db_session: Session,
) -> None:
    instrument = _insert_instrument(db_session)
    _insert_candles(db_session, instrument.id, count=60)

    summary = calculate_default_indicators_for_instrument(
        db_session,
        instrument_id=instrument.id,
        timeframe="1d",
    )

    expected_names = {
        "sma_20",
        "ema_20",
        "rsi_14",
        "macd_12_26_9",
        "bollinger_bands_20_2",
        "atr_14",
    }
    stored_names = set(
        db_session.scalars(select(IndicatorValue.indicator_name)).all(),
    )

    assert set(summary["indicator_names"]) == expected_names
    assert expected_names <= stored_names


def _candle_frame(close_prices: list[float | int]) -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-01", periods=len(close_prices), freq="D")
    closes = pd.Series(close_prices, dtype="float")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes - 0.5,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": [1000 + index for index in range(len(close_prices))],
        }
    )


def _insert_instrument(db_session: Session) -> Instrument:
    instrument = Instrument(
        ticker="SBER",
        name="Sberbank",
        market="shares",
        board="TQBR",
        currency="RUB",
        is_active=True,
    )
    db_session.add(instrument)
    db_session.flush()
    return instrument


def _insert_candles(
    db_session: Session,
    instrument_id: int,
    *,
    count: int,
) -> None:
    start = datetime.fromisoformat("2024-01-01T00:00:00")
    candles = []
    for index in range(count):
        close = Decimal("100") + Decimal(index)
        candles.append(
            {
                "timeframe": "1d",
                "timestamp": start + timedelta(days=index),
                "open": close - Decimal("0.50"),
                "high": close + Decimal("1.00"),
                "low": close - Decimal("1.00"),
                "close": close,
                "volume": Decimal(1000 + index),
            }
        )
    candle_repository.bulk_upsert_candles(
        db_session,
        instrument_id=instrument_id,
        candles=candles,
    )
