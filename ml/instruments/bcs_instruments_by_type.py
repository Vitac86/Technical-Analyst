from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "ml" / "data" / "instruments"
REPORT_DIR = REPO_ROOT / "ml" / "reports" / "instruments"

BCS_BASE_URL = os.getenv("BCS_BASE_URL", "https://be.broker.ru").rstrip("/")
BCS_TOKEN_URL = os.getenv(
    "BCS_TOKEN_URL",
    f"{BCS_BASE_URL}/trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token",
)
BCS_INFORMATION_BASE_URL = os.getenv(
    "BCS_INFORMATION_BASE_URL",
    f"{BCS_BASE_URL}/trade-api-information-service",
).rstrip("/")

# Official docs page:
# https://trade-api.bcs.ru/http/information/get-instruments-by-type-and-base-asset-ticker/
BCS_INSTRUMENTS_BY_TYPE_PATH = os.getenv(
    "BCS_INSTRUMENTS_BY_TYPE_PATH",
    "/api/v1/instruments/by-type",
)
BCS_INSTRUMENTS_BY_TICKERS_PATH = os.getenv(
    "BCS_INSTRUMENTS_BY_TICKERS_PATH",
    "/api/v1/instruments/by-tickers",
)

# The docs currently call this parameter "size". Keep it isolated because older
# notes and generated clients may refer to the same concept as "limit".
BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM = os.getenv(
    "BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM",
    "size",
)

DEFAULT_CLIENT_ID = "trade-api-read"
DEFAULT_LIMIT = 50
DEFAULT_MAX_PAGES = 200
REQUEST_TIMEOUT_SECONDS = 30

BCS_INSTRUMENT_TYPES = [
    "CURRENCY",
    "STOCK",
    "FOREIGN_STOCK",
    "BONDS",
    "NOTES",
    "DEPOSITARY_RECEIPTS",
    "EURO_BONDS",
    "MUTUAL_FUNDS",
    "ETF",
    "FUTURES",
    "OPTIONS",
    "GOODS",
    "INDICES",
]


class BcsError(RuntimeError):
    """Base class for BCS offline discovery errors."""


class BcsAuthError(BcsError):
    """BCS OAuth token exchange failed."""


class BcsRateLimitError(BcsError):
    """BCS returned HTTP 429."""


class BcsHttpError(BcsError):
    """BCS returned an unexpected HTTP response."""


@dataclass
class BcsFetchResult:
    instrument_type: str
    instruments: list[dict[str, Any]]
    endpoint_base_url: str
    endpoint_path: str
    endpoint_url: str
    page_size_param: str
    limit: int
    pages: list[dict[str, int]]
    raw_sample_keys: list[str]
    generated_at: str
    truncated: bool = False
    base_asset_ticker: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def join_url(base_url: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return base_url.rstrip("/") + normalized_path


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def safe_error_body(response: requests.Response, max_chars: int = 1000) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:max_chars].replace("\n", " ")

    def sanitize(value: Any) -> Any:
        if isinstance(value, dict):
            clean: dict[str, Any] = {}
            for key, item in value.items():
                if "token" in str(key).lower():
                    clean[key] = "<hidden>"
                else:
                    clean[key] = sanitize(item)
            return clean
        if isinstance(value, list):
            return [sanitize(item) for item in value[:5]]
        return value

    return json.dumps(sanitize(payload), ensure_ascii=False, sort_keys=True)[:max_chars]


def get_access_token(
    refresh_token: str | None = None,
    client_id: str | None = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> str:
    refresh_token = (refresh_token or os.getenv("BCS_REFRESH_TOKEN", "")).strip()
    if not refresh_token:
        raise BcsAuthError(
            "BCS_REFRESH_TOKEN is empty. Set it in the environment before running discovery."
        )

    client_id = (client_id or os.getenv("BCS_CLIENT_ID", DEFAULT_CLIENT_ID)).strip()
    if not client_id:
        client_id = DEFAULT_CLIENT_ID

    try:
        response = requests.post(
            BCS_TOKEN_URL,
            data={
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise BcsAuthError(f"Network error exchanging BCS refresh token: {type(exc).__name__}") from exc

    if response.status_code == 429:
        raise BcsRateLimitError("BCS auth rate limit reached (HTTP 429). Retry later.")
    if not response.ok:
        raise BcsAuthError(
            f"BCS auth failed: HTTP {response.status_code}. {safe_error_body(response)}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise BcsAuthError("BCS auth response was not valid JSON.") from exc

    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise BcsAuthError("BCS auth response did not contain access_token.")

    return access_token


def auth_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def get_case_insensitive(raw: dict[str, Any], key: str) -> Any:
    if key in raw:
        return raw[key]
    lower = key.lower()
    for raw_key, value in raw.items():
        if str(raw_key).lower() == lower:
            return value
    return None


def first_value(raw: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = get_case_insensitive(raw, key)
        if value is not None and value != "":
            return value
    return None


def clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clean_upper(value: Any) -> str | None:
    text = clean_string(value)
    return text.upper() if text else None


def to_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if value == value else None
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    return None


def normalize_board(raw_board: Any) -> dict[str, str] | str | None:
    if isinstance(raw_board, dict):
        class_code = clean_upper(
            first_value(raw_board, ["classCode", "class_code", "board", "boardCode"])
        )
        exchange = clean_upper(first_value(raw_board, ["exchange", "exchangeCode"]))
        board: dict[str, str] = {}
        if class_code:
            board["classCode"] = class_code
        if exchange:
            board["exchange"] = exchange
        return board or None

    text = clean_upper(raw_board)
    return text if text else None


def board_class_code(board: dict[str, Any] | str) -> str | None:
    if isinstance(board, dict):
        return clean_upper(board.get("classCode") or board.get("board") or board.get("boardCode"))
    return clean_upper(board)


def normalize_boards(raw: dict[str, Any]) -> list[dict[str, str] | str]:
    boards: list[dict[str, str] | str] = []
    seen: set[str] = set()

    def append_board(value: Any) -> None:
        board = normalize_board(value)
        if board is None:
            return
        key = json.dumps(board, ensure_ascii=False, sort_keys=True)
        if key in seen:
            return
        seen.add(key)
        boards.append(board)

    raw_boards = first_value(raw, ["boards", "boardCodes", "boardList"])
    if isinstance(raw_boards, list):
        for board in raw_boards:
            append_board(board)
    elif raw_boards is not None:
        append_board(raw_boards)

    root_class_code = clean_upper(
        first_value(raw, ["classCode", "class_code", "securityClassCode"])
    )
    if root_class_code and not any(board_class_code(board) == root_class_code for board in boards):
        append_board({"classCode": root_class_code})

    primary_board = clean_upper(first_value(raw, ["primaryBoard", "primary_board"]))
    if primary_board and not any(board_class_code(board) == primary_board for board in boards):
        append_board({"classCode": primary_board})

    secondary_boards = first_value(raw, ["secondaryBoards", "secondary_boards"])
    if isinstance(secondary_boards, list):
        for board in secondary_boards:
            append_board({"classCode": board})

    return boards


def normalize_instrument(
    raw: dict[str, Any],
    source_type: str,
    include_raw: bool = False,
) -> dict[str, Any]:
    boards = normalize_boards(raw)
    first_board_class = next(
        (class_code for class_code in (board_class_code(board) for board in boards) if class_code),
        None,
    )

    class_code = clean_upper(
        first_value(raw, ["classCode", "class_code", "securityClassCode"])
    ) or first_board_class

    primary_board = clean_upper(first_value(raw, ["primaryBoard", "primary_board"])) or class_code

    instrument = {
        "ticker": clean_upper(first_value(raw, ["ticker", "symbol", "secCode", "secid"])),
        "classCode": class_code,
        "primaryBoard": primary_board,
        "boards": boards,
        "displayName": clean_string(
            first_value(raw, ["displayName", "display_name", "name", "fullName"])
        ),
        "shortName": clean_string(first_value(raw, ["shortName", "short_name", "shortname"])),
        "instrumentType": clean_upper(first_value(raw, ["instrumentType", "instrument_type"])),
        "type": clean_upper(first_value(raw, ["type"])),
        "isin": clean_upper(first_value(raw, ["isin", "ISIN"])),
        "tradingCurrency": clean_upper(
            first_value(raw, ["tradingCurrency", "trading_currency", "currency"])
        ),
        "settlementCurrency": clean_upper(
            first_value(raw, ["settlementCurrency", "settlement_currency", "currencyNominal"])
        ),
        "lotSize": to_number(first_value(raw, ["lotSize", "lot_size", "lotsize"])),
        "minimumStep": to_number(
            first_value(raw, ["minimumStep", "minimum_step", "minStep", "priceStep"])
        ),
        "scale": to_number(first_value(raw, ["scale", "decimals", "precision"])),
        "sourceType": source_type,
    }

    if include_raw:
        instrument["raw"] = raw

    return instrument


def extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    candidates: list[Any] = [
        payload.get("items"),
        payload.get("data"),
        payload.get("content"),
        payload.get("result"),
    ]

    for container_key in ("data", "result", "payload", "page"):
        container = payload.get(container_key)
        if isinstance(container, dict):
            candidates.extend(
                [
                    container.get("items"),
                    container.get("content"),
                    container.get("data"),
                    container.get("result"),
                ]
            )

    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]

    return []


def first_number_from_paths(payload: dict[str, Any], paths: list[tuple[str, ...]]) -> int | None:
    for path in paths:
        current: Any = payload
        for part in path:
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        number = to_number(current)
        if isinstance(number, int):
            return number
    return None


def metadata_says_has_more(payload: Any, page: int, item_count: int, limit: int) -> bool | None:
    if not isinstance(payload, dict):
        return None

    for key in ("hasNext", "hasNextPage", "hasMore"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value

    for key in ("last", "isLast", "lastPage"):
        value = payload.get(key)
        if isinstance(value, bool):
            return not value

    for container_key in ("page", "pagination", "meta", "metadata"):
        container = payload.get(container_key)
        if not isinstance(container, dict):
            continue

        for key in ("hasNext", "hasNextPage", "hasMore"):
            value = container.get(key)
            if isinstance(value, bool):
                return value
        for key in ("last", "isLast", "lastPage"):
            value = container.get(key)
            if isinstance(value, bool):
                return not value

    total_pages = first_number_from_paths(
        payload,
        [
            ("totalPages",),
            ("page", "totalPages"),
            ("pagination", "totalPages"),
            ("meta", "totalPages"),
            ("metadata", "totalPages"),
        ],
    )
    if total_pages is not None:
        return page + 1 < total_pages

    total_items = first_number_from_paths(
        payload,
        [
            ("totalElements",),
            ("totalItems",),
            ("total",),
            ("page", "totalElements"),
            ("pagination", "totalElements"),
            ("pagination", "total"),
            ("meta", "total"),
            ("metadata", "total"),
        ],
    )
    if total_items is not None:
        return (page + 1) * limit < total_items

    if item_count == 0 or item_count < limit:
        return False

    return None


def request_json(
    method: str,
    url: str,
    access_token: str,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> Any:
    try:
        response = requests.request(
            method,
            url,
            headers={**auth_headers(access_token), **kwargs.pop("headers", {})},
            timeout=timeout,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise BcsHttpError(f"Network error calling BCS information endpoint: {type(exc).__name__}") from exc

    if response.status_code == 429:
        raise BcsRateLimitError(f"BCS information endpoint rate limit reached (HTTP 429): {url}")
    if response.status_code in (401, 403):
        raise BcsAuthError(f"BCS information endpoint rejected the access token: HTTP {response.status_code}")
    if not response.ok:
        raise BcsHttpError(
            f"BCS information endpoint failed: HTTP {response.status_code}. {safe_error_body(response)}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise BcsHttpError("BCS information endpoint response was not valid JSON.") from exc


def raw_sample_keys(items: list[dict[str, Any]], limit: int = 5) -> list[str]:
    keys: set[str] = set()
    for item in items[:limit]:
        keys.update(str(key) for key in item.keys())
    return sorted(keys)


def fetch_instruments_by_type(
    access_token: str,
    instrument_type: str,
    limit: int = DEFAULT_LIMIT,
    max_pages: int = DEFAULT_MAX_PAGES,
    base_asset_ticker: str | None = None,
    include_raw: bool = False,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    page_size_param: str = BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
) -> BcsFetchResult:
    instrument_type = instrument_type.strip().upper()
    if instrument_type not in BCS_INSTRUMENT_TYPES:
        raise ValueError(
            f"Unsupported BCS instrument type {instrument_type!r}. "
            f"Expected one of: {', '.join(BCS_INSTRUMENT_TYPES)}"
        )
    if instrument_type == "OPTIONS" and not base_asset_ticker:
        raise ValueError("type=OPTIONS requires baseAssetTicker.")
    if limit < 1:
        raise ValueError("limit must be >= 1.")
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1.")

    endpoint_url = join_url(BCS_INFORMATION_BASE_URL, BCS_INSTRUMENTS_BY_TYPE_PATH)
    effective_base_asset_ticker = (
        base_asset_ticker.strip().upper()
        if instrument_type == "OPTIONS" and base_asset_ticker
        else None
    )
    instruments: list[dict[str, Any]] = []
    pages: list[dict[str, int]] = []
    sample_keys: set[str] = set()
    truncated = True

    for page in range(max_pages):
        params: dict[str, Any] = {
            "type": instrument_type,
            "page": page,
            page_size_param: limit,
        }
        if effective_base_asset_ticker:
            params["baseAssetTicker"] = effective_base_asset_ticker

        payload = request_json(
            "GET",
            endpoint_url,
            access_token,
            params=params,
            timeout=timeout,
        )
        items = extract_items(payload)
        sample_keys.update(raw_sample_keys(items))
        pages.append({"page": page, "count": len(items)})

        instruments.extend(
            normalize_instrument(item, source_type=instrument_type, include_raw=include_raw)
            for item in items
        )

        has_more = metadata_says_has_more(payload, page=page, item_count=len(items), limit=limit)
        if len(items) == 0 or len(items) < limit or has_more is False:
            truncated = False
            break

    return BcsFetchResult(
        instrument_type=instrument_type,
        instruments=instruments,
        endpoint_base_url=BCS_INFORMATION_BASE_URL,
        endpoint_path=BCS_INSTRUMENTS_BY_TYPE_PATH,
        endpoint_url=endpoint_url,
        page_size_param=page_size_param,
        limit=limit,
        pages=pages,
        raw_sample_keys=sorted(sample_keys),
        generated_at=utc_now_iso(),
        truncated=truncated,
        base_asset_ticker=effective_base_asset_ticker,
    )


def chunks(values: list[str], chunk_size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), chunk_size):
        yield values[index : index + chunk_size]


def fetch_instruments_by_tickers(
    access_token: str,
    tickers: Iterable[str],
    include_raw: bool = False,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    chunk_size: int = 50,
) -> list[dict[str, Any]]:
    endpoint_url = join_url(BCS_INFORMATION_BASE_URL, BCS_INSTRUMENTS_BY_TICKERS_PATH)
    normalized_tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not normalized_tickers:
        return []

    instruments: list[dict[str, Any]] = []
    for ticker_chunk in chunks(normalized_tickers, chunk_size):
        payload = request_json(
            "POST",
            endpoint_url,
            access_token,
            json={"tickers": ticker_chunk},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        items = extract_items(payload)
        instruments.extend(
            normalize_instrument(item, source_type="CANDIDATE_TICKER", include_raw=include_raw)
            for item in items
        )

    return dedupe_instruments(instruments)


def dedupe_instruments(instruments: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for instrument in instruments:
        key = (
            instrument.get("ticker"),
            instrument.get("classCode"),
            instrument.get("isin"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(instrument)
    return result


def counter_to_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def value_distribution(instruments: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for instrument in instruments:
        value = clean_string(instrument.get(key)) or "<missing>"
        counter[value] += 1
    return counter_to_dict(counter)


def boards_distribution(instruments: Iterable[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for instrument in instruments:
        boards = instrument.get("boards")
        if not isinstance(boards, list) or not boards:
            counter["<missing>"] += 1
            continue
        for board in boards:
            counter[board_class_code(board) or "<missing>"] += 1
    return counter_to_dict(counter)


def currency_distribution(instruments: Iterable[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for instrument in instruments:
        currency = (
            clean_string(instrument.get("tradingCurrency"))
            or clean_string(instrument.get("settlementCurrency"))
            or "<missing>"
        )
        counter[currency] += 1
    return counter_to_dict(counter)


def suggested_moex_match_keys(instruments: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    keys: list[dict[str, Any]] = []
    for instrument in instruments:
        keys.append(
            {
                "ticker": instrument.get("ticker"),
                "classCode": instrument.get("classCode"),
                "isin": instrument.get("isin"),
            }
        )
    return keys


def build_type_summary(result: BcsFetchResult, sample_size: int = 10) -> dict[str, Any]:
    instruments = result.instruments
    return {
        "generatedAt": result.generated_at,
        "type": result.instrument_type,
        "endpointBaseUrl": result.endpoint_base_url,
        "endpointPath": result.endpoint_path,
        "endpointUrl": result.endpoint_url,
        "pageSizeParam": result.page_size_param,
        "limit": result.limit,
        "pages": result.pages,
        "truncatedByMaxPages": result.truncated,
        "baseAssetTicker": result.base_asset_ticker,
        "total": len(instruments),
        "rawSampleKeys": result.raw_sample_keys,
        "classCodeDistribution": value_distribution(instruments, "classCode"),
        "boardsDistribution": boards_distribution(instruments),
        "instrumentTypeDistribution": value_distribution(instruments, "instrumentType"),
        "typeDistribution": value_distribution(instruments, "type"),
        "currencyDistribution": currency_distribution(instruments),
        "normalizedSample": instruments[:sample_size],
    }


def write_type_outputs(result: BcsFetchResult) -> tuple[Path, Path]:
    data_path = DATA_DIR / f"bcs_{result.instrument_type}.json"
    report_path = REPORT_DIR / f"bcs_{result.instrument_type}_summary.json"
    save_json(data_path, result.instruments)
    save_json(report_path, build_type_summary(result))
    return data_path, report_path


def write_aggregate_summary(
    results: Iterable[BcsFetchResult],
    skipped: list[dict[str, str]] | None = None,
    errors: list[dict[str, str]] | None = None,
) -> Path:
    result_list = list(results)
    page_size_param = (
        result_list[0].page_size_param if result_list else BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM
    )
    payload = {
        "generatedAt": utc_now_iso(),
        "endpointBaseUrl": BCS_INFORMATION_BASE_URL,
        "endpointPath": BCS_INSTRUMENTS_BY_TYPE_PATH,
        "pageSizeParam": page_size_param,
        "types": {
            result.instrument_type: {
                "total": len(result.instruments),
                "pages": result.pages,
                "truncatedByMaxPages": result.truncated,
                "dataFile": str(DATA_DIR / f"bcs_{result.instrument_type}.json"),
                "summaryFile": str(REPORT_DIR / f"bcs_{result.instrument_type}_summary.json"),
            }
            for result in result_list
        },
        "skipped": skipped or [],
        "errors": errors or [],
    }
    path = REPORT_DIR / "bcs_instruments_by_type_summary.json"
    save_json(path, payload)
    return path


def print_result(result: BcsFetchResult, data_path: Path, report_path: Path) -> None:
    print(
        f"{result.instrument_type}: {len(result.instruments)} instruments "
        f"from {result.endpoint_path} ({result.page_size_param}={result.limit})"
    )
    print(f"Data: {data_path}")
    print(f"Summary: {report_path}")
    if result.instruments:
        print("Sample:")
        for instrument in result.instruments[:5]:
            print(
                "  - "
                f"{instrument.get('ticker') or '<missing>'} | "
                f"{instrument.get('classCode') or '<missing>'} | "
                f"{instrument.get('displayName') or instrument.get('shortName') or '<no name>'}"
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch BCS instruments by official information-service instrument type."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--type", dest="instrument_type", help="BCS instrument type, e.g. GOODS.")
    mode.add_argument(
        "--all-types",
        action="store_true",
        help="Fetch all documented types. OPTIONS is skipped unless --base-asset-ticker is set.",
    )
    parser.add_argument("--base-asset-ticker", help="Required by BCS for type=OPTIONS.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Records per page.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Pagination safety cap.")
    parser.add_argument("--include-raw", action="store_true", help="Include raw BCS item in data output.")
    parser.add_argument(
        "--page-size-param",
        default=BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
        help="BCS page-size query parameter name. Official docs currently use 'size'.",
    )
    return parser.parse_args(argv)


def run_single_type(
    access_token: str,
    instrument_type: str,
    args: argparse.Namespace,
) -> BcsFetchResult:
    result = fetch_instruments_by_type(
        access_token,
        instrument_type=instrument_type,
        limit=args.limit,
        max_pages=args.max_pages,
        base_asset_ticker=args.base_asset_ticker,
        include_raw=args.include_raw,
        page_size_param=args.page_size_param,
    )
    data_path, report_path = write_type_outputs(result)
    print_result(result, data_path, report_path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    requested_type = (args.instrument_type or "GOODS").strip().upper()

    try:
        access_token = get_access_token()

        if args.all_types:
            results: list[BcsFetchResult] = []
            skipped: list[dict[str, str]] = []
            errors: list[dict[str, str]] = []

            for instrument_type in BCS_INSTRUMENT_TYPES:
                if instrument_type == "OPTIONS" and not args.base_asset_ticker:
                    skipped.append(
                        {
                            "type": "OPTIONS",
                            "reason": "BCS requires baseAssetTicker for OPTIONS.",
                        }
                    )
                    continue
                try:
                    results.append(run_single_type(access_token, instrument_type, args))
                except BcsRateLimitError:
                    raise
                except BcsAuthError:
                    raise
                except Exception as exc:
                    errors.append({"type": instrument_type, "error": str(exc)})
                    print(f"{instrument_type}: skipped after error: {exc}", file=sys.stderr)

            aggregate_path = write_aggregate_summary(results, skipped=skipped, errors=errors)
            print(f"Aggregate summary: {aggregate_path}")
            return 1 if errors else 0

        result = run_single_type(access_token, requested_type, args)
        aggregate_path = write_aggregate_summary([result])
        print(f"Aggregate summary: {aggregate_path}")
        return 0

    except BcsRateLimitError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (BcsError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
