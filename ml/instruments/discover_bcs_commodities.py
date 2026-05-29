from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

try:
    from .bcs_instruments_by_type import (
        BCS_INFORMATION_BASE_URL,
        BCS_INSTRUMENTS_BY_TYPE_PATH,
        BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
        BcsError,
        BcsRateLimitError,
        board_class_code,
        boards_distribution,
        clean_string,
        currency_distribution,
        dedupe_instruments,
        fetch_instruments_by_tickers,
        fetch_instruments_by_type,
        get_access_token,
        save_json,
        suggested_moex_match_keys,
        utc_now_iso,
        value_distribution,
        write_type_outputs,
    )
except ImportError:
    from bcs_instruments_by_type import (  # type: ignore
        BCS_INFORMATION_BASE_URL,
        BCS_INSTRUMENTS_BY_TYPE_PATH,
        BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
        BcsError,
        BcsRateLimitError,
        board_class_code,
        boards_distribution,
        clean_string,
        currency_distribution,
        dedupe_instruments,
        fetch_instruments_by_tickers,
        fetch_instruments_by_type,
        get_access_token,
        save_json,
        suggested_moex_match_keys,
        utc_now_iso,
        value_distribution,
        write_type_outputs,
    )


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATES_PATH = REPO_ROOT / "ml" / "config" / "commodity_candidates.yaml"
REPORT_PATH = REPO_ROOT / "ml" / "reports" / "instruments" / "bcs_commodities.json"

KEYWORD_TAGS: dict[str, list[str]] = {
    "gold": ["gold", "gld", "xau", "\u0437\u043e\u043b\u043e\u0442"],
    "silver": ["silver", "slv", "xag", "\u0441\u0435\u0440\u0435\u0431"],
    "platinum": ["platinum", "plt", "xpt", "\u043f\u043b\u0430\u0442\u0438\u043d"],
    "palladium": ["palladium", "pda", "xpd", "\u043f\u0430\u043b\u043b\u0430\u0434"],
    "oil": ["oil", "brent", "crud", "\u043d\u0435\u0444\u0442"],
    "gas": ["gas", "gaz", "\u0433\u0430\u0437"],
    "metals": ["metal", "\u043c\u0435\u0442\u0430\u043b", "copper", "\u043c\u0435\u0434"],
}


def load_candidates(path: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not path.exists():
        return [], {}

    text = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(text) if yaml is not None else parse_simple_candidates_yaml(text)
    payload = payload or {}
    if not isinstance(payload, dict):
        return [], {}

    raw_tickers = payload.get("tickers") or payload.get("commodityTickers") or []
    tickers = [
        str(ticker).strip().upper()
        for ticker in raw_tickers
        if str(ticker).strip()
    ]

    raw_overrides = payload.get("manualOverrides") or payload.get("manual_overrides") or {}
    overrides: dict[str, dict[str, Any]] = {}
    if isinstance(raw_overrides, dict):
        for ticker, value in raw_overrides.items():
            normalized_ticker = str(ticker).strip().upper()
            if not normalized_ticker:
                continue
            if isinstance(value, dict):
                overrides[normalized_ticker] = value
            elif isinstance(value, list):
                overrides[normalized_ticker] = {"tags": value}

    return sorted(set(tickers)), overrides


def parse_simple_candidates_yaml(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"tickers": [], "manualOverrides": {}}
    section: str | None = None
    current_override: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        stripped = line.strip()
        if not line.startswith(" ") and stripped.endswith(":"):
            section = stripped[:-1]
            current_override = None
            continue

        if section == "tickers" and stripped.startswith("- "):
            payload["tickers"].append(stripped[2:].strip())
            continue

        if section == "manualOverrides":
            if line.startswith("  ") and not line.startswith("    ") and stripped.endswith(":"):
                current_override = stripped[:-1].strip()
                payload["manualOverrides"][current_override] = {}
                continue

            if current_override and stripped.startswith("tags:"):
                raw_tags = stripped.split(":", 1)[1].strip()
                if raw_tags.startswith("[") and raw_tags.endswith("]"):
                    tags = [
                        tag.strip().strip("'\"")
                        for tag in raw_tags[1:-1].split(",")
                        if tag.strip()
                    ]
                else:
                    tags = [raw_tags] if raw_tags else []
                payload["manualOverrides"][current_override]["tags"] = tags

    return payload


def text_for_tags(instrument: dict[str, Any]) -> str:
    parts = [
        instrument.get("ticker"),
        instrument.get("displayName"),
        instrument.get("shortName"),
        instrument.get("instrumentType"),
        instrument.get("type"),
        instrument.get("sourceType"),
    ]
    return " ".join(str(part) for part in parts if part is not None).lower()


def infer_commodity_tags(
    instrument: dict[str, Any],
    manual_overrides: dict[str, dict[str, Any]],
) -> list[str]:
    ticker = str(instrument.get("ticker") or "").upper()
    tags: set[str] = set()
    source_type = str(instrument.get("sourceType") or "").upper()
    instrument_type = str(instrument.get("instrumentType") or instrument.get("type") or "").upper()

    if source_type == "GOODS" or instrument_type == "GOODS":
        tags.update(["commodity", "bcs_goods"])

    override = manual_overrides.get(ticker)
    if override:
        raw_tags = override.get("tags", [])
        if isinstance(raw_tags, list):
            tags.update(str(tag).strip() for tag in raw_tags if str(tag).strip())

    haystack = text_for_tags(instrument)
    for tag, keywords in KEYWORD_TAGS.items():
        if any(keyword in haystack for keyword in keywords):
            tags.add("commodity")
            tags.add(tag)
            if tag in {"gold", "silver", "platinum", "palladium"}:
                tags.add("precious_metals")

    return sorted(tags)


def with_commodity_tags(
    instruments: Iterable[dict[str, Any]],
    manual_overrides: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    tagged: list[dict[str, Any]] = []
    for instrument in instruments:
        next_instrument = dict(instrument)
        next_instrument["commodityTags"] = infer_commodity_tags(next_instrument, manual_overrides)
        tagged.append(next_instrument)
    return tagged


def is_commodity_like(instrument: dict[str, Any]) -> bool:
    tags = instrument.get("commodityTags")
    return isinstance(tags, list) and "commodity" in tags


def compact_error(exc: Exception) -> str:
    return str(exc)[:1000]


def class_codes_for(instrument: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    class_code = clean_string(instrument.get("classCode"))
    if class_code:
        codes.add(class_code)

    boards = instrument.get("boards")
    if isinstance(boards, list):
        for board in boards:
            code = board_class_code(board)
            if code:
                codes.add(code)

    return codes


def find_ambiguous_instruments(instruments: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ambiguous: list[dict[str, Any]] = []
    for instrument in instruments:
        reasons: list[str] = []
        ticker = clean_string(instrument.get("ticker"))
        codes = class_codes_for(instrument)
        if not ticker:
            reasons.append("missing_ticker")
        if not codes:
            reasons.append("missing_class_code")
        if len(codes) > 1:
            reasons.append("multiple_class_codes")
        if reasons:
            ambiguous.append(
                {
                    "ticker": instrument.get("ticker"),
                    "classCode": instrument.get("classCode"),
                    "isin": instrument.get("isin"),
                    "reasons": reasons,
                }
            )
    return ambiguous


def tag_distribution(instruments: Iterable[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for instrument in instruments:
        tags = instrument.get("commodityTags")
        if not isinstance(tags, list) or not tags:
            counter["<missing>"] += 1
            continue
        for tag in tags:
            counter[str(tag)] += 1
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def build_report(
    instruments: list[dict[str, Any]],
    direct_goods_endpoint_used: bool,
    endpoint_path: str,
    fallback_reason: str | None,
    candidate_tickers: list[str],
    raw_sample_keys: list[str],
) -> dict[str, Any]:
    normalized_sample_keys = sorted(
        {
            str(key)
            for instrument in instruments[:5]
            for key in instrument.keys()
            if key != "raw"
        }
    )
    return {
        "generatedAt": utc_now_iso(),
        "directGoodsEndpointUsed": direct_goods_endpoint_used,
        "endpointBaseUrl": BCS_INFORMATION_BASE_URL,
        "endpointPath": endpoint_path,
        "pageSizeParam": BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
        "fallbackReason": fallback_reason,
        "candidateTickersUsed": [] if direct_goods_endpoint_used else candidate_tickers,
        "totalGoods": len(instruments) if direct_goods_endpoint_used else 0,
        "totalCommodities": len(instruments),
        "rawSampleKeys": raw_sample_keys,
        "normalizedSampleKeys": normalized_sample_keys,
        "normalizedSample": instruments[:10],
        "classCodeDistribution": value_distribution(instruments, "classCode"),
        "boardsDistribution": boards_distribution(instruments),
        "instrumentTypeDistribution": value_distribution(instruments, "instrumentType"),
        "currencyDistribution": currency_distribution(instruments),
        "commodityTagDistribution": tag_distribution(instruments),
        "ambiguousInstruments": find_ambiguous_instruments(instruments),
        "suggestedMoexMatchKeys": suggested_moex_match_keys(instruments),
        "instruments": instruments,
    }


def discover_bcs_commodities(
    limit: int,
    max_pages: int,
    include_raw: bool,
    candidates_path: Path,
) -> dict[str, Any]:
    candidate_tickers, manual_overrides = load_candidates(candidates_path)
    access_token = get_access_token()

    direct_goods_endpoint_used = False
    fallback_reason: str | None = None
    raw_sample_keys: list[str] = []
    instruments: list[dict[str, Any]]

    try:
        goods_result = fetch_instruments_by_type(
            access_token,
            instrument_type="GOODS",
            limit=limit,
            max_pages=max_pages,
            include_raw=include_raw,
        )
        write_type_outputs(goods_result)
        instruments = goods_result.instruments
        raw_sample_keys = goods_result.raw_sample_keys
        direct_goods_endpoint_used = True
    except BcsRateLimitError:
        raise
    except BcsError as exc:
        fallback_reason = compact_error(exc)
        if not candidate_tickers:
            raise BcsError(
                f"GOODS endpoint failed and no fallback candidate tickers were found: {fallback_reason}"
            ) from exc

        fallback_instruments = fetch_instruments_by_tickers(
            access_token,
            candidate_tickers,
            include_raw=include_raw,
        )
        instruments = [
            instrument
            for instrument in with_commodity_tags(fallback_instruments, manual_overrides)
            if is_commodity_like(instrument)
        ]
    else:
        instruments = with_commodity_tags(instruments, manual_overrides)

    instruments = dedupe_instruments(instruments)
    report = build_report(
        instruments=instruments,
        direct_goods_endpoint_used=direct_goods_endpoint_used,
        endpoint_path=BCS_INSTRUMENTS_BY_TYPE_PATH,
        fallback_reason=fallback_reason,
        candidate_tickers=candidate_tickers,
        raw_sample_keys=raw_sample_keys,
    )
    save_json(REPORT_PATH, report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover BCS commodities using GOODS first, then candidate by-tickers fallback."
    )
    parser.add_argument("--limit", type=int, default=50, help="Records per page for GOODS discovery.")
    parser.add_argument("--max-pages", type=int, default=200, help="Pagination safety cap.")
    parser.add_argument("--include-raw", action="store_true", help="Include raw BCS items in reports.")
    parser.add_argument(
        "--candidates",
        type=Path,
        default=DEFAULT_CANDIDATES_PATH,
        help="YAML file with fallback commodity ticker candidates and manual tags.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = discover_bcs_commodities(
            limit=args.limit,
            max_pages=args.max_pages,
            include_raw=args.include_raw,
            candidates_path=args.candidates,
        )
    except BcsRateLimitError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: {compact_error(exc)}")
        return 1

    print(f"BCS commodities report: {REPORT_PATH}")
    print(f"directGoodsEndpointUsed: {json.dumps(report['directGoodsEndpointUsed'])}")
    print(f"endpointPath: {report['endpointPath']}")
    print(f"totalGoods: {report['totalGoods']}")
    print(f"totalCommodities: {report['totalCommodities']}")
    print("classCodeDistribution:")
    for class_code, count in list(report["classCodeDistribution"].items())[:10]:
        print(f"  - {class_code}: {count}")
    if report["normalizedSample"]:
        print("Sample:")
        for instrument in report["normalizedSample"][:5]:
            print(
                "  - "
                f"{instrument.get('ticker') or '<missing>'} | "
                f"{instrument.get('classCode') or '<missing>'} | "
                f"{instrument.get('displayName') or instrument.get('shortName') or '<no name>'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
