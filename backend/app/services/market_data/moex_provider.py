from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.services.market_data.base import MarketDataProvider


MOEX_BASE_URL = "https://iss.moex.com/iss"
MOEX_SHARES_BOARD = "TQBR"
MOEX_MARKET = "shares"
MOEX_TIME_ZONE = ZoneInfo("Europe/Moscow")

SUPPORTED_TIMEFRAMES: dict[str, int] = {
    "1m": 1,
    "10m": 10,
    "1h": 60,
    "1d": 24,
    "1w": 7,
    "1mo": 31,
}


def timeframe_to_interval(timeframe: str) -> int:
    """Map the app timeframe string to a MOEX ISS candle interval."""
    normalized = timeframe.strip()
    try:
        return SUPPORTED_TIMEFRAMES[normalized]
    except KeyError as exc:
        supported = ", ".join(SUPPORTED_TIMEFRAMES)
        raise ValueError(
            f"Unsupported MOEX timeframe '{timeframe}'. "
            f"Supported timeframes: {supported}."
        ) from exc


def extract_table_rows(payload: dict[str, Any], table_name: str) -> list[dict[str, Any]]:
    """Return ISS table rows as dictionaries keyed by column name."""
    table = payload.get(table_name)
    if not isinstance(table, dict):
        return []

    columns = table.get("columns")
    data = table.get("data")
    if not isinstance(columns, list) or not isinstance(data, list):
        return []

    rows: list[dict[str, Any]] = []
    for raw_row in data:
        if not isinstance(raw_row, list):
            continue
        rows.append(dict(zip(columns, raw_row, strict=False)))
    return rows


def normalize_instrument_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize MOEX ISS securities rows into app instrument dictionaries."""
    instruments: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()

    for row in extract_table_rows(payload, "securities"):
        ticker = _upper_string(row.get("SECID"))
        if ticker is None or ticker in seen_tickers:
            continue

        seen_tickers.add(ticker)
        name = (
            _clean_string(row.get("SECNAME"))
            or _clean_string(row.get("SHORTNAME"))
            or ticker
        )
        instruments.append(
            {
                "ticker": ticker,
                "name": name,
                "market": MOEX_MARKET,
                "board": _upper_string(row.get("BOARDID")) or MOEX_SHARES_BOARD,
                "currency": _normalize_currency(
                    _upper_string(row.get("CURRENCYID"))
                    or _upper_string(row.get("FACEUNIT"))
                ),
                "is_active": _is_active_security(row),
            }
        )

    return instruments


def normalize_candle_rows(
    payload: dict[str, Any],
    ticker: str,
    timeframe: str,
) -> list[dict[str, Any]]:
    """Normalize MOEX ISS candle rows into app candle dictionaries."""
    timeframe_to_interval(timeframe)
    normalized_ticker = ticker.strip().upper()
    candles: list[dict[str, Any]] = []

    for row in extract_table_rows(payload, "candles"):
        candle = _normalize_candle_row(row, normalized_ticker, timeframe)
        if candle is not None:
            candles.append(candle)

    return candles


class MoexProvider(MarketDataProvider):
    """MOEX ISS provider for Russian stock market shares."""

    name = "moex"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        base_url: str = MOEX_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def fetch_instruments(self) -> list[dict[str, Any]]:
        """Fetch active MOEX TQBR share instruments."""
        async with self._client_context() as client:
            payload = await self._get_json(
                client,
                f"/engines/stock/markets/{MOEX_MARKET}/boards/{MOEX_SHARES_BOARD}/securities.json",
                params={
                    "iss.meta": "off",
                    "iss.only": "securities",
                    "securities.columns": (
                        "SECID,SHORTNAME,SECNAME,BOARDID,STATUS,IS_TRADED,"
                        "CURRENCYID,FACEUNIT"
                    ),
                },
            )
        return normalize_instrument_rows(payload)

    async def fetch_candles(
        self,
        ticker: str,
        timeframe: str,
        start: date | datetime,
        end: date | datetime,
    ) -> list[dict[str, Any]]:
        """Fetch MOEX candles for a ticker, following ISS pagination."""
        interval = timeframe_to_interval(timeframe)
        normalized_ticker = ticker.strip().upper()
        if not normalized_ticker:
            raise ValueError("Ticker must not be empty.")

        candles: list[dict[str, Any]] = []
        start_index = 0

        async with self._client_context() as client:
            while True:
                payload = await self._get_json(
                    client,
                    (
                        f"/engines/stock/markets/{MOEX_MARKET}/boards/"
                        f"{MOEX_SHARES_BOARD}/securities/{normalized_ticker}/candles.json"
                    ),
                    params={
                        "iss.meta": "off",
                        "iss.only": "candles,candles.cursor",
                        "candles.columns": (
                            "begin,end,open,high,low,close,volume,value"
                        ),
                        "from": _format_date(start),
                        "till": _format_date(end),
                        "interval": interval,
                        "start": start_index,
                    },
                )

                page = normalize_candle_rows(payload, normalized_ticker, timeframe)
                candles.extend(page)

                next_start = _next_page_start(payload, start_index)
                if next_start is None:
                    break
                start_index = next_start

        return candles

    @asynccontextmanager
    async def _client_context(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return

        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={"User-Agent": "technical-analyst/0.1"},
        ) as client:
            yield client

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        response = await client.get(path, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return {}
        return payload


def _normalize_candle_row(
    row: dict[str, Any],
    ticker: str,
    timeframe: str,
) -> dict[str, Any] | None:
    timestamp = _parse_timestamp(row.get("begin"))
    open_ = _decimal_or_none(row.get("open"))
    high = _decimal_or_none(row.get("high"))
    low = _decimal_or_none(row.get("low"))
    close = _decimal_or_none(row.get("close"))

    if None in (timestamp, open_, high, low, close):
        return None

    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": _decimal_or_none(row.get("volume")),
    }


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=MOEX_TIME_ZONE)
    return parsed


def _decimal_or_none(value: Any) -> Decimal | None:
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


def _upper_string(value: Any) -> str | None:
    cleaned = _clean_string(value)
    return cleaned.upper() if cleaned is not None else None


def _normalize_currency(value: str | None) -> str | None:
    if value in {"SUR", "RUR"}:
        return "RUB"
    return value


def _is_active_security(row: dict[str, Any]) -> bool:
    status = _upper_string(row.get("STATUS"))
    is_traded = row.get("IS_TRADED")

    active = True
    if status is not None:
        active = status in {"A", "ACTIVE", "1", "Y", "YES", "TRUE"}

    if is_traded is not None:
        active = active and str(is_traded).strip().upper() in {
            "1",
            "Y",
            "YES",
            "TRUE",
        }

    return active


def _format_date(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _next_page_start(payload: dict[str, Any], current_start: int) -> int | None:
    cursor_rows = extract_table_rows(payload, "candles.cursor")
    if not cursor_rows:
        return None

    cursor = cursor_rows[0]
    index = _int_or_none(cursor.get("INDEX"))
    total = _int_or_none(cursor.get("TOTAL"))
    page_size = _int_or_none(cursor.get("PAGESIZE"))
    if index is None or total is None or page_size is None or page_size <= 0:
        return None

    next_start = index + page_size
    if next_start <= current_start or next_start >= total:
        return None
    return next_start


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
