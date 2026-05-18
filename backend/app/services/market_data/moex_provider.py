from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.services.market_data.base import MarketDataProvider


MOEX_BASE_URL = "https://iss.moex.com/iss"
MOEX_ENGINE = "stock"
MOEX_MARKET = "shares"
MOEX_SHARES_BOARD = "TQBR"
MOEX_TIME_ZONE = ZoneInfo("Europe/Moscow")

# MOEX ISS candle interval codes
MOEX_TIMEFRAME_MAP: dict[str, int] = {
    "1m": 1,
    "10m": 10,
    "1h": 60,
    "1d": 24,
    "1w": 7,
    "1mo": 31,
}

# Known MOEX security group → (engine, market) mapping.
# Groups follow the pattern "{engine}_{market}" which we parse directly,
# but explicit entries here override parsing for clarity.
_GROUP_ENGINE_MARKET: dict[str, tuple[str, str]] = {
    "stock_shares": ("stock", "shares"),
    "stock_bonds": ("stock", "bonds"),
    "stock_dr": ("stock", "shares"),
    "stock_etf": ("stock", "shares"),
    "stock_ppif": ("stock", "shares"),
    "stock_index": ("stock", "ndm"),
    "currency_selt": ("currency", "selt"),
    "currency_metal": ("currency", "metal"),
    "futures_forts": ("futures", "forts"),
    "futures_options": ("futures", "options"),
}

# Known primary_boardid → default board for use when group is unavailable.
_BOARD_DEFAULTS: dict[str, tuple[str, str, str]] = {
    "TQBR": ("stock", "shares", "TQBR"),
    "TQBS": ("stock", "shares", "TQBS"),
    "TQNL": ("stock", "shares", "TQNL"),
    "TQOB": ("stock", "bonds", "TQOB"),
    "TQCB": ("stock", "bonds", "TQCB"),
    "CETS": ("currency", "selt", "CETS"),
    "RFUD": ("futures", "forts", "RFUD"),
}


@dataclass(frozen=True)
class MoexInstrumentSource:
    """Full MOEX ISS address for one instrument."""

    engine: str
    market: str
    board: str
    ticker: str


def timeframe_to_interval(timeframe: str) -> int:
    """Map a MOEX timeframe string to its ISS interval code."""
    normalized = timeframe.strip()
    try:
        return MOEX_TIMEFRAME_MAP[normalized]
    except KeyError as exc:
        supported = ", ".join(MOEX_TIMEFRAME_MAP)
        raise ValueError(
            f"Unsupported MOEX timeframe '{timeframe}'. "
            f"Supported timeframes: {supported}."
        ) from exc


def extract_table_rows(payload: dict[str, Any], table_name: str) -> list[dict[str, Any]]:
    """Return ISS table rows as dicts keyed by column name."""
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
    """Normalize MOEX ISS securities rows (TQBR board) into app instrument dicts."""
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
                "engine": MOEX_ENGINE,
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
    """Normalize MOEX ISS candle rows into app candle dicts."""
    timeframe_to_interval(timeframe)
    normalized_ticker = ticker.strip().upper()
    candles: list[dict[str, Any]] = []

    for row in extract_table_rows(payload, "candles"):
        candle = _normalize_candle_row(row, normalized_ticker, timeframe)
        if candle is not None:
            candles.append(candle)

    return candles


def normalize_search_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize MOEX ISS security search results into instrument candidate dicts.

    Uses the ``group`` field to derive engine/market and ``primary_boardid`` as
    the board. Currency is not available in search results and is left as None.
    """
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in extract_table_rows(payload, "securities"):
        ticker = _upper_string(row.get("secid"))
        if ticker is None or ticker in seen:
            continue
        seen.add(ticker)

        name = (
            _clean_string(row.get("name"))
            or _clean_string(row.get("shortname"))
            or ticker
        )
        group = _clean_string(row.get("group")) or ""
        board = _upper_string(row.get("primary_boardid")) or ""
        engine, market = _resolve_engine_market(group, board)
        is_active = _coerce_is_traded(row.get("is_traded"))

        results.append(
            {
                "ticker": ticker,
                "name": name,
                "engine": engine,
                "market": market,
                "board": board or None,
                "currency": None,
                "is_active": is_active,
                "group": group or None,
            }
        )

    return results


class MoexProvider(MarketDataProvider):
    """MOEX ISS market data provider."""

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def find_instruments(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search MOEX for instruments matching *query* without syncing the full market.

        Returns candidates suitable for user selection (ticker, name, engine,
        market, board, is_active, group). Currency is not available at search
        time and is returned as None.
        """
        async with self._client_context() as client:
            payload = await self._get_json(
                client,
                "/securities.json",
                params={
                    "q": query.strip(),
                    "iss.meta": "off",
                    "lang": "ru",
                    "limit": limit,
                    "securities.columns": (
                        "secid,shortname,name,is_traded,type,group,"
                        "primary_boardid,marketprice_boardid"
                    ),
                },
            )
        return normalize_search_rows(payload)

    async def fetch_instrument(
        self,
        source: MoexInstrumentSource,
    ) -> dict[str, Any] | None:
        """Fetch metadata for one specific instrument from MOEX ISS.

        Returns an instrument dict compatible with the repository upsert,
        or None if not found.
        """
        async with self._client_context() as client:
            payload = await self._get_json(
                client,
                (
                    f"/engines/{source.engine}/markets/{source.market}"
                    f"/boards/{source.board}/securities/{source.ticker}.json"
                ),
                params={
                    "iss.meta": "off",
                    "iss.only": "securities",
                    "securities.columns": (
                        "SECID,SHORTNAME,SECNAME,BOARDID,STATUS,IS_TRADED,"
                        "CURRENCYID,FACEUNIT"
                    ),
                },
            )
        rows = normalize_instrument_rows(payload)
        for row in rows:
            if row["ticker"] == source.ticker.strip().upper():
                row["engine"] = source.engine
                row["market"] = source.market
                return row
        # If not in rows, build a minimal entry from the source tuple.
        if rows:
            row = rows[0]
            row["engine"] = source.engine
            row["market"] = source.market
            return row
        return None

    async def fetch_instruments(self) -> list[dict[str, Any]]:
        """Fetch active MOEX TQBR share instruments (legacy full-market sync)."""
        async with self._client_context() as client:
            payload = await self._get_json(
                client,
                f"/engines/{MOEX_ENGINE}/markets/{MOEX_MARKET}/boards/{MOEX_SHARES_BOARD}/securities.json",
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
        """Fetch MOEX candles for a ticker on the default TQBR board."""
        source = MoexInstrumentSource(
            engine=MOEX_ENGINE,
            market=MOEX_MARKET,
            board=MOEX_SHARES_BOARD,
            ticker=ticker.strip().upper(),
        )
        return await self.fetch_candles_by_source(source, timeframe, start, end)

    async def fetch_candles_by_source(
        self,
        source: MoexInstrumentSource,
        timeframe: str,
        start: date | datetime,
        end: date | datetime,
    ) -> list[dict[str, Any]]:
        """Fetch MOEX candles for any engine/market/board/ticker combination."""
        interval = timeframe_to_interval(timeframe)
        normalized_ticker = source.ticker.strip().upper()
        if not normalized_ticker:
            raise ValueError("Ticker must not be empty.")

        candles: list[dict[str, Any]] = []
        start_index = 0
        path = (
            f"/engines/{source.engine}/markets/{source.market}"
            f"/boards/{source.board}/securities/{normalized_ticker}/candles.json"
        )

        async with self._client_context() as client:
            while True:
                payload = await self._get_json(
                    client,
                    path,
                    params={
                        "iss.meta": "off",
                        "iss.only": "candles,candles.cursor",
                        "candles.columns": "begin,end,open,high,low,close,volume,value",
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _resolve_engine_market(group: str, board: str) -> tuple[str | None, str | None]:
    """Derive (engine, market) from security group string or board fallback."""
    if group and group in _GROUP_ENGINE_MARKET:
        return _GROUP_ENGINE_MARKET[group]
    if group and "_" in group:
        parts = group.split("_", 1)
        return parts[0], parts[1]
    if board and board in _BOARD_DEFAULTS:
        eng, mkt, _ = _BOARD_DEFAULTS[board]
        return eng, mkt
    return None, None


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


def _coerce_is_traded(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip() in {"1", "true", "True", "yes", "Yes"}


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
