"""Tests for the technical signals engine."""
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models as _models  # noqa: F401
from app.db.base import Base
from app.db.models.candle import Candle
from app.db.models.indicator_value import IndicatorValue
from app.db.models.instrument import Instrument
from app.db.session import get_db
from app.main import app
from app.services.analysis.signal_engine import (
    _evaluate_atr,
    _evaluate_bollinger,
    _evaluate_ema,
    _evaluate_macd,
    _evaluate_rsi,
    _evaluate_sma,
    _aggregate_signals,
    generate_technical_signals,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session() -> Iterator[Session]:
    # StaticPool ensures the same in-memory connection is reused across threads
    # (needed when TestClient dispatches requests to a worker thread).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with testing_session() as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def test_client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TS = datetime(2024, 1, 15, 0, 0, 0)


def _iv(indicator_name: str, values: dict) -> IndicatorValue:
    return IndicatorValue(
        id=None,
        instrument_id=1,
        indicator_name=indicator_name,
        category="trend",
        timeframe="1d",
        timestamp=_TS,
        values=values,
    )


# ---------------------------------------------------------------------------
# SMA tests
# ---------------------------------------------------------------------------


def test_sma_buy_when_close_above_threshold() -> None:
    rows = [_iv("sma_20", {"value": 100.0})]
    result = _evaluate_sma(101.0, rows)  # +1.0% above threshold
    assert result.signal == "buy"
    assert result.score == 1


def test_sma_sell_when_close_below_threshold() -> None:
    rows = [_iv("sma_20", {"value": 100.0})]
    result = _evaluate_sma(99.0, rows)  # -1.0% below threshold
    assert result.signal == "sell"
    assert result.score == -1


def test_sma_neutral_when_close_near_sma() -> None:
    rows = [_iv("sma_20", {"value": 100.0})]
    result = _evaluate_sma(100.1, rows)  # +0.1% within ±0.3%
    assert result.signal == "neutral"
    assert result.score == 0


def test_sma_neutral_when_no_data() -> None:
    result = _evaluate_sma(100.0, [])
    assert result.signal == "neutral"
    assert result.score == 0
    assert result.value is None


# ---------------------------------------------------------------------------
# EMA tests
# ---------------------------------------------------------------------------


def test_ema_buy_when_close_above_threshold() -> None:
    rows = [_iv("ema_20", {"value": 100.0})]
    result = _evaluate_ema(102.0, rows)
    assert result.signal == "buy"
    assert result.score == 1


def test_ema_sell_when_close_below_threshold() -> None:
    rows = [_iv("ema_20", {"value": 100.0})]
    result = _evaluate_ema(98.0, rows)
    assert result.signal == "sell"
    assert result.score == -1


def test_ema_neutral_when_close_near_ema() -> None:
    rows = [_iv("ema_20", {"value": 100.0})]
    result = _evaluate_ema(100.2, rows)
    assert result.signal == "neutral"
    assert result.score == 0


# ---------------------------------------------------------------------------
# RSI tests
# ---------------------------------------------------------------------------


def test_rsi_buy_when_oversold() -> None:
    rows = [_iv("rsi_14", {"value": 25.0})]
    result = _evaluate_rsi(rows)
    assert result.signal == "buy"
    assert result.score == 1
    assert result.strength == "medium"


def test_rsi_caution_when_overbought() -> None:
    rows = [_iv("rsi_14", {"value": 75.0})]
    result = _evaluate_rsi(rows)
    assert result.signal == "caution"
    assert result.score == -1


def test_rsi_neutral_when_in_midrange() -> None:
    rows = [_iv("rsi_14", {"value": 50.0})]
    result = _evaluate_rsi(rows)
    assert result.signal == "neutral"
    assert result.score == 0


def test_rsi_buy_with_positive_momentum() -> None:
    rows = [_iv("rsi_14", {"value": 60.0})]
    result = _evaluate_rsi(rows)
    assert result.signal == "buy"
    assert result.score == 1
    assert result.strength == "weak"


def test_rsi_sell_with_weak_momentum() -> None:
    rows = [_iv("rsi_14", {"value": 38.0})]
    result = _evaluate_rsi(rows)
    assert result.signal == "sell"
    assert result.score == -1


# ---------------------------------------------------------------------------
# MACD tests
# ---------------------------------------------------------------------------


def _macd_iv(macd: float, signal: float, histogram: float, ts_offset: int = 0) -> IndicatorValue:
    return IndicatorValue(
        id=None,
        instrument_id=1,
        indicator_name="macd_12_26_9",
        category="trend",
        timeframe="1d",
        timestamp=_TS + timedelta(days=ts_offset),
        values={"macd": macd, "signal": signal, "histogram": histogram},
    )


def test_macd_bullish_histogram_crossover() -> None:
    rows = [
        _macd_iv(macd=-0.5, signal=0.0, histogram=-0.5, ts_offset=0),  # prev
        _macd_iv(macd=0.3, signal=0.1, histogram=0.2, ts_offset=1),    # latest
    ]
    result = _evaluate_macd(rows)
    assert result.signal == "buy"
    assert result.score == 2
    assert result.strength == "strong"


def test_macd_bearish_histogram_crossover() -> None:
    rows = [
        _macd_iv(macd=0.5, signal=0.2, histogram=0.3, ts_offset=0),    # prev
        _macd_iv(macd=-0.1, signal=0.1, histogram=-0.2, ts_offset=1),  # latest
    ]
    result = _evaluate_macd(rows)
    assert result.signal == "sell"
    assert result.score == -2
    assert result.strength == "strong"


def test_macd_bullish_directional_bias() -> None:
    rows = [_macd_iv(macd=0.5, signal=0.2, histogram=0.3)]
    result = _evaluate_macd(rows)
    assert result.signal == "buy"
    assert result.score == 2
    assert result.strength == "medium"


def test_macd_bearish_directional_bias() -> None:
    rows = [_macd_iv(macd=-0.3, signal=0.0, histogram=-0.3)]
    result = _evaluate_macd(rows)
    assert result.signal == "sell"
    assert result.score == -2
    assert result.strength == "medium"


# ---------------------------------------------------------------------------
# Bollinger Bands tests
# ---------------------------------------------------------------------------


def _bb_iv(upper: float, middle: float, lower: float, percent_b: float) -> IndicatorValue:
    return _iv(
        "bollinger_bands_20_2",
        {"upper": upper, "middle": middle, "lower": lower, "percent_b": percent_b},
    )


def test_bollinger_buy_when_close_below_lower() -> None:
    rows = [_bb_iv(upper=110.0, middle=100.0, lower=90.0, percent_b=-0.1)]
    result = _evaluate_bollinger(88.0, rows)  # below lower band
    assert result.signal == "buy"
    assert result.score == 1
    assert result.strength == "medium"


def test_bollinger_caution_when_close_above_upper() -> None:
    rows = [_bb_iv(upper=110.0, middle=100.0, lower=90.0, percent_b=1.1)]
    result = _evaluate_bollinger(112.0, rows)  # above upper band
    assert result.signal == "caution"
    assert result.score == -1
    assert result.strength == "medium"


def test_bollinger_neutral_when_close_inside_bands() -> None:
    rows = [_bb_iv(upper=110.0, middle=100.0, lower=90.0, percent_b=0.5)]
    result = _evaluate_bollinger(100.0, rows)
    assert result.signal == "neutral"
    assert result.score == 0


def test_bollinger_caution_near_upper_by_percent_b() -> None:
    rows = [_bb_iv(upper=110.0, middle=100.0, lower=90.0, percent_b=0.95)]
    result = _evaluate_bollinger(109.0, rows)
    assert result.signal == "caution"
    assert result.strength == "weak"


def test_bollinger_buy_near_lower_by_percent_b() -> None:
    rows = [_bb_iv(upper=110.0, middle=100.0, lower=90.0, percent_b=0.05)]
    result = _evaluate_bollinger(91.0, rows)
    assert result.signal == "buy"
    assert result.strength == "weak"


# ---------------------------------------------------------------------------
# ATR tests
# ---------------------------------------------------------------------------


def test_atr_returns_info_signal() -> None:
    rows = [_iv("atr_14", {"value": 2.0})]
    result = _evaluate_atr(100.0, rows)
    assert result.signal == "info"
    assert result.score == 0


def test_atr_includes_atr_percent_in_value() -> None:
    rows = [_iv("atr_14", {"value": 2.0})]
    result = _evaluate_atr(100.0, rows)
    assert isinstance(result.value, dict)
    assert "atr_percent" in result.value
    assert result.value["atr_percent"] == pytest.approx(2.0, rel=1e-3)


def test_atr_low_volatility_strength() -> None:
    rows = [_iv("atr_14", {"value": 0.5})]
    result = _evaluate_atr(100.0, rows)  # 0.5% ATR
    assert result.strength == "weak"


def test_atr_medium_volatility_strength() -> None:
    rows = [_iv("atr_14", {"value": 2.0})]
    result = _evaluate_atr(100.0, rows)  # 2.0% ATR
    assert result.strength == "medium"


def test_atr_elevated_volatility_strength() -> None:
    rows = [_iv("atr_14", {"value": 5.0})]
    result = _evaluate_atr(100.0, rows)  # 5.0% ATR
    assert result.strength == "strong"


# ---------------------------------------------------------------------------
# Aggregate scoring tests
# ---------------------------------------------------------------------------


def _make_signal(sig: str, score: int) -> object:
    """Return a minimal TechnicalSignalItem-like object for aggregate tests."""
    from app.schemas.analysis import TechnicalSignalItem

    return TechnicalSignalItem(
        indicator_name="test",
        label="Test",
        value=None,
        signal=sig,  # type: ignore[arg-type]
        score=score,
        strength="weak",
        reason="test",
        timestamp=None,
    )


def test_aggregate_strong_buy_when_score_gte_4() -> None:
    signals = [
        _make_signal("buy", 2),
        _make_signal("buy", 2),
        _make_signal("neutral", 0),
        _make_signal("info", 0),
    ]
    agg = _aggregate_signals(1, "1d", signals)  # type: ignore[arg-type]
    assert agg.signal == "strong_buy"
    assert agg.total_score == 4


def test_aggregate_buy_when_score_2_or_3() -> None:
    signals = [
        _make_signal("buy", 2),
        _make_signal("neutral", 0),
        _make_signal("info", 0),
    ]
    agg = _aggregate_signals(1, "1d", signals)  # type: ignore[arg-type]
    assert agg.signal == "buy"


def test_aggregate_neutral_when_score_between_minus1_and_1() -> None:
    signals = [
        _make_signal("buy", 1),
        _make_signal("sell", -1),
        _make_signal("info", 0),
    ]
    agg = _aggregate_signals(1, "1d", signals)  # type: ignore[arg-type]
    assert agg.signal == "neutral"
    assert agg.total_score == 0


def test_aggregate_sell_when_score_minus2_or_minus3() -> None:
    signals = [
        _make_signal("sell", -2),
        _make_signal("neutral", 0),
        _make_signal("info", 0),
    ]
    agg = _aggregate_signals(1, "1d", signals)  # type: ignore[arg-type]
    assert agg.signal == "sell"


def test_aggregate_strong_sell_when_score_lte_minus4() -> None:
    signals = [
        _make_signal("sell", -2),
        _make_signal("sell", -2),
        _make_signal("info", 0),
    ]
    agg = _aggregate_signals(1, "1d", signals)  # type: ignore[arg-type]
    assert agg.signal == "strong_sell"


def test_aggregate_caution_override_when_two_caution_signals_and_positive_score() -> None:
    signals = [
        _make_signal("buy", 2),
        _make_signal("caution", -1),
        _make_signal("caution", -1),
    ]
    _aggregate_signals(1, "1d", signals)  # type: ignore[arg-type]
    # total_score = 0, caution_count = 2 but score not > 0 so no override; just check no crash
    # Retest with net positive score
    signals2 = [
        _make_signal("buy", 2),
        _make_signal("buy", 1),
        _make_signal("caution", -1),
        _make_signal("caution", -1),
    ]
    agg2 = _aggregate_signals(1, "1d", signals2)  # type: ignore[arg-type]
    assert agg2.signal == "caution"
    assert agg2.total_score == 1


def test_aggregate_confidence_high_with_5_actionable() -> None:
    signals = [_make_signal("buy", 1) for _ in range(5)]
    agg = _aggregate_signals(1, "1d", signals)  # type: ignore[arg-type]
    assert agg.confidence == "high"


def test_aggregate_confidence_medium_with_3_actionable() -> None:
    signals = [_make_signal("buy", 1) for _ in range(3)]
    agg = _aggregate_signals(1, "1d", signals)  # type: ignore[arg-type]
    assert agg.confidence == "medium"


def test_aggregate_confidence_low_with_2_actionable() -> None:
    signals = [_make_signal("buy", 1) for _ in range(2)]
    agg = _aggregate_signals(1, "1d", signals)  # type: ignore[arg-type]
    assert agg.confidence == "low"


# ---------------------------------------------------------------------------
# API endpoint test
# ---------------------------------------------------------------------------


def _insert_instrument(session: Session) -> Instrument:
    instrument = Instrument(
        ticker="TEST",
        name="Test Instrument",
        market="shares",
        board="TQBR",
        currency="RUB",
        is_active=True,
    )
    session.add(instrument)
    session.flush()
    return instrument


def _insert_candle(session: Session, instrument_id: int) -> Candle:
    candle = Candle(
        instrument_id=instrument_id,
        timeframe="1d",
        timestamp=_TS,
        open=Decimal("100.00"),
        high=Decimal("105.00"),
        low=Decimal("98.00"),
        close=Decimal("102.00"),
        volume=Decimal("50000"),
    )
    session.add(candle)
    session.flush()
    return candle


def _insert_indicator(
    session: Session,
    instrument_id: int,
    indicator_name: str,
    category: str,
    values: dict,
) -> IndicatorValue:
    iv = IndicatorValue(
        instrument_id=instrument_id,
        indicator_name=indicator_name,
        category=category,
        timeframe="1d",
        timestamp=_TS,
        values=values,
    )
    session.add(iv)
    session.flush()
    return iv


def test_technical_signals_endpoint_returns_expected_shape(
    test_client: TestClient,
    db_session: Session,
) -> None:
    instrument = _insert_instrument(db_session)
    _insert_candle(db_session, instrument.id)
    _insert_indicator(db_session, instrument.id, "sma_20", "trend", {"value": 100.0})
    _insert_indicator(db_session, instrument.id, "ema_20", "trend", {"value": 99.5})
    _insert_indicator(db_session, instrument.id, "rsi_14", "momentum", {"value": 55.0})
    _insert_indicator(
        db_session,
        instrument.id,
        "macd_12_26_9",
        "trend",
        {"macd": 0.5, "signal": 0.2, "histogram": 0.3},
    )
    _insert_indicator(
        db_session,
        instrument.id,
        "bollinger_bands_20_2",
        "trend",
        {"upper": 110.0, "middle": 100.0, "lower": 90.0, "percent_b": 0.6},
    )
    _insert_indicator(db_session, instrument.id, "atr_14", "volatility", {"value": 2.0})
    db_session.commit()

    response = test_client.get(
        f"/api/v1/analysis/technical-signals?instrument_id={instrument.id}&timeframe=1d"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["instrument_id"] == instrument.id
    assert data["timeframe"] == "1d"
    assert "aggregate" in data
    assert "signals" in data
    assert len(data["signals"]) == 6

    indicator_names = {s["indicator_name"] for s in data["signals"]}
    assert indicator_names == {
        "sma_20", "ema_20", "rsi_14", "macd_12_26_9", "bollinger_bands_20_2", "atr_14"
    }

    agg = data["aggregate"]
    assert agg["instrument_id"] == instrument.id
    assert agg["timeframe"] == "1d"
    assert "total_score" in agg
    assert "signal" in agg
    assert "confidence" in agg
    assert "generated_at" in agg


# ---------------------------------------------------------------------------
# No-candle edge case tests
# ---------------------------------------------------------------------------


def test_no_candle_returns_no_data_signal(db_session: Session) -> None:
    """generate_technical_signals returns no_data when no candle exists."""
    instrument = _insert_instrument(db_session)
    db_session.commit()

    result = generate_technical_signals(db_session, instrument_id=instrument.id, timeframe="1d")

    assert result.aggregate.signal == "no_data"
    assert result.aggregate.confidence == "low"
    assert result.signals == []
    assert result.message is not None
    assert "candle" in result.message.lower()


def test_no_candle_does_not_fabricate_close_zero(db_session: Session) -> None:
    """No indicator evaluation should run when candle is absent (close=0.0 bug)."""
    instrument = _insert_instrument(db_session)
    _insert_indicator(db_session, instrument.id, "sma_20", "trend", {"value": 100.0})
    _insert_indicator(db_session, instrument.id, "bollinger_bands_20_2", "trend", {
        "upper": 110.0, "middle": 100.0, "lower": 90.0, "percent_b": 0.5,
    })
    db_session.commit()

    result = generate_technical_signals(db_session, instrument_id=instrument.id, timeframe="1d")

    assert result.aggregate.signal == "no_data"
    assert result.signals == []


def test_no_candle_endpoint_returns_no_data(
    test_client: TestClient,
    db_session: Session,
) -> None:
    """HTTP endpoint returns no_data aggregate when no candle exists for instrument."""
    instrument = _insert_instrument(db_session)
    db_session.commit()

    response = test_client.get(
        f"/api/v1/analysis/technical-signals?instrument_id={instrument.id}&timeframe=1d"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["aggregate"]["signal"] == "no_data"
    assert data["signals"] == []
    assert data["message"] is not None
