"""Tests for MOEX quote snapshot normalizer and endpoint."""
import pytest

from app.services.market_data.moex_provider import normalize_quote_rows


# ---------------------------------------------------------------------------
# Helpers — minimal MOEX ISS payload builders
# ---------------------------------------------------------------------------


def _moex_payload(
    secid: str = "SBER",
    prevprice: float | None = 295.5,
    last: float | None = 296.0,
    bid: float | None = 295.8,
    offer: float | None = 296.2,
    open_: float | None = 294.0,
    high: float | None = 298.0,
    low: float | None = 293.0,
    close: float | None = None,
    lasttoprevprice: float | None = 0.17,
    voltoday: float | None = 1_234_567.0,
    valtoday: float | None = 364_899_456.0,
    updatetime: str | None = "10:35:42",
    systime: str | None = "2024-01-15 10:35:43",
) -> dict:
    return {
        "securities": {
            "columns": ["SECID", "BOARDID", "PREVPRICE", "PREVDATE"],
            "data": [[secid, "TQBR", prevprice, "2024-01-12"]],
        },
        "marketdata": {
            "columns": [
                "SECID", "BOARDID", "LAST", "BID", "OFFER", "OPEN", "HIGH", "LOW",
                "CLOSE", "LASTTOPREVPRICE", "VOLTODAY", "VALTODAY", "UPDATETIME", "SYSTIME",
            ],
            "data": [[
                secid, "TQBR", last, bid, offer, open_, high, low,
                close, lasttoprevprice, voltoday, valtoday, updatetime, systime,
            ]],
        },
    }


def _empty_payload() -> dict:
    return {
        "securities": {"columns": [], "data": []},
        "marketdata": {"columns": [], "data": []},
    }


# ---------------------------------------------------------------------------
# normalize_quote_rows tests
# ---------------------------------------------------------------------------


def test_normalize_quote_rows_returns_expected_fields() -> None:
    payload = _moex_payload()
    result = normalize_quote_rows(payload, ticker="SBER", engine="stock", market="shares", board="TQBR")

    assert result["ticker"] == "SBER"
    assert result["engine"] == "stock"
    assert result["market"] == "shares"
    assert result["board"] == "TQBR"
    assert result["source"] == "moex"
    assert result["last_price"] == pytest.approx(296.0)
    assert result["bid"] == pytest.approx(295.8)
    assert result["ask"] == pytest.approx(296.2)
    assert result["open"] == pytest.approx(294.0)
    assert result["high"] == pytest.approx(298.0)
    assert result["low"] == pytest.approx(293.0)
    assert result["previous_close"] == pytest.approx(295.5)
    assert result["change_percent"] == pytest.approx(0.17)
    assert result["volume"] == pytest.approx(1_234_567.0)
    assert result["value"] == pytest.approx(364_899_456.0)
    assert result["trade_time"] == "10:35:42"
    assert result["server_time"] == "2024-01-15 10:35:43"


def test_normalize_quote_rows_computes_change_from_last_and_prevprice() -> None:
    payload = _moex_payload(last=296.0, prevprice=295.5)
    result = normalize_quote_rows(payload, ticker="SBER", engine="stock", market="shares", board="TQBR")

    assert result["change"] == pytest.approx(0.5, abs=1e-4)


def test_normalize_quote_rows_handles_null_last_price() -> None:
    payload = _moex_payload(last=None)
    result = normalize_quote_rows(payload, ticker="SBER", engine="stock", market="shares", board="TQBR")

    assert result["last_price"] is None
    assert result["change"] is None


def test_normalize_quote_rows_handles_null_prevprice() -> None:
    payload = _moex_payload(prevprice=None)
    result = normalize_quote_rows(payload, ticker="SBER", engine="stock", market="shares", board="TQBR")

    assert result["previous_close"] is None
    assert result["change"] is None


def test_normalize_quote_rows_handles_empty_marketdata() -> None:
    """When MOEX returns no data rows all price fields must be None, not crash."""
    payload = _empty_payload()
    result = normalize_quote_rows(payload, ticker="SBER", engine="stock", market="shares", board="TQBR")

    assert result["ticker"] == "SBER"
    assert result["source"] == "moex"
    assert result["last_price"] is None
    assert result["bid"] is None
    assert result["ask"] is None
    assert result["volume"] is None
    assert result["trade_time"] is None
    assert result["server_time"] is None


def test_normalize_quote_rows_ticker_uppercased() -> None:
    payload = _moex_payload(secid="SBER")
    result = normalize_quote_rows(payload, ticker="sber", engine="stock", market="shares", board="TQBR")
    assert result["ticker"] == "SBER"


def test_normalize_quote_rows_close_field_may_be_none_during_session() -> None:
    payload = _moex_payload(close=None)
    result = normalize_quote_rows(payload, ticker="SBER", engine="stock", market="shares", board="TQBR")
    assert result["close"] is None
