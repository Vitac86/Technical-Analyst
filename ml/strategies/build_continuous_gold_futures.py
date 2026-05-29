"""
Stitch downloaded gold futures contracts into a continuous series.

Inputs
------
* Discovery JSON:    ``ml/reports/strategies/gold_futures_discovery.json``
* Availability JSON: ``ml/reports/strategies/gold_futures_availability.json``
* Raw CSVs:          ``ml/data/raw_bcs/<TICKER>_<CLASSCODE>_<TIMEFRAME>.csv``

Outputs (per timeframe)
-----------------------
* ``ml/data/processed_bcs/GOLD_FUT_CONTINUOUS_<TIMEFRAME>.csv``

Plus a single summary report:
* ``ml/reports/strategies/gold_futures_continuous_summary.json``

Stitching method (initial)
--------------------------
Unadjusted concatenation:

* Sort contracts by maturity/expiration date when available; otherwise
  parse a ``YYYY-MM`` or ``-M.YY`` style date from the ticker.
* For every timestamp keep at most one bar; when contracts overlap prefer
  the contract whose maturity is closer to (but not before) that
  timestamp — i.e. the "front" contract at that point in time.
* Record gaps between consecutive bars and the number of overlap
  resolutions.

This is an **unadjusted** continuous series; roll gaps will distort
indicators and backtest PnL. The summary report carries a prominent
warning to that effect.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


DISCOVERY_PATH = _REPO_ROOT / "ml" / "reports" / "strategies" / "gold_futures_discovery.json"
AVAILABILITY_PATH = _REPO_ROOT / "ml" / "reports" / "strategies" / "gold_futures_availability.json"
RAW_DIR = _REPO_ROOT / "ml" / "data" / "raw_bcs"
PROCESSED_DIR = _REPO_ROOT / "ml" / "data" / "processed_bcs"
REPORT_DIR = _REPO_ROOT / "ml" / "reports" / "strategies"

TIMEFRAME_DELTA_SECONDS = {
    "M5": 5 * 60,
    "M15": 15 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D": 24 * 60 * 60,
}

# Map BCS-style month codes (e.g. 3.26 -> March 2026).
_TICKER_DATE_RE = re.compile(r"(?P<month>\d{1,2})\.(?P<year>\d{2,4})")
_TICKER_YYMMDD_RE = re.compile(r"(?P<year>20\d{2})[-_]?(?P<month>\d{2})")


WARNING_TEXT = (
    "This is an unadjusted continuous futures series. "
    "Roll gaps may affect indicators and backtest results."
)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class ContractInfo:
    ticker: str
    classCode: str
    maturity: datetime | None
    inferred_from: str  # "maturityDate" | "expirationDate" | "ticker_pattern" | "none"


def _parse_iso(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _maturity_from_ticker(ticker: str) -> datetime | None:
    m = _TICKER_DATE_RE.search(ticker)
    if m:
        try:
            month = int(m.group("month"))
            year_raw = int(m.group("year"))
            year = 2000 + year_raw if year_raw < 100 else year_raw
            if 1 <= month <= 12 and 2000 <= year <= 2100:
                return datetime(year, month, 15, tzinfo=timezone.utc)
        except ValueError:
            pass
    m = _TICKER_YYMMDD_RE.search(ticker)
    if m:
        try:
            year = int(m.group("year"))
            month = int(m.group("month"))
            if 1 <= month <= 12:
                return datetime(year, month, 15, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def load_candidates(path: Path) -> list[ContractInfo]:
    if not path.exists():
        raise FileNotFoundError(
            f"Discovery file not found at {path}. "
            "Run `python ml/strategies/discover_gold_futures.py` first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_list = payload.get("candidates") or []
    contracts: list[ContractInfo] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        class_code = str(raw.get("classCode") or "").strip().upper()
        if not ticker or not class_code:
            continue
        key = (ticker, class_code)
        if key in seen:
            continue
        seen.add(key)
        maturity = _parse_iso(raw.get("maturityDate"))
        inferred_from = "maturityDate" if maturity else ""
        if maturity is None:
            maturity = _parse_iso(raw.get("expirationDate"))
            inferred_from = "expirationDate" if maturity else inferred_from
        if maturity is None:
            maturity = _parse_iso(raw.get("settlementDate"))
            inferred_from = "settlementDate" if maturity else inferred_from
        if maturity is None:
            maturity = _maturity_from_ticker(ticker)
            inferred_from = "ticker_pattern" if maturity else "none"
        contracts.append(
            ContractInfo(
                ticker=ticker,
                classCode=class_code,
                maturity=maturity,
                inferred_from=inferred_from or "none",
            )
        )
    return contracts


# ---------------------------------------------------------------------------
# Load OHLC CSV
# ---------------------------------------------------------------------------


@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_ticker: str
    source_class_code: str


def _coerce_ts(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_bars(path: Path, ticker: str, class_code: str) -> list[Bar]:
    if not path.exists():
        return []
    bars: list[Bar] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = _coerce_ts(row.get("timestamp", ""))
            if ts is None:
                continue
            try:
                o = float(row["open"])
                h = float(row["high"])
                lo = float(row["low"])
                c = float(row["close"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                v = float(row.get("volume") or 0.0)
            except (TypeError, ValueError):
                v = 0.0
            if not (h >= lo and min(o, h, lo, c) > 0):
                continue
            bars.append(
                Bar(
                    timestamp=ts,
                    open=o,
                    high=h,
                    low=lo,
                    close=c,
                    volume=v,
                    source_ticker=ticker,
                    source_class_code=class_code,
                )
            )
    bars.sort(key=lambda b: b.timestamp)
    return bars


# ---------------------------------------------------------------------------
# Stitching
# ---------------------------------------------------------------------------


@dataclass
class StitchStats:
    contracts_used: list[dict[str, Any]] = field(default_factory=list)
    overlaps_removed: int = 0
    duplicates_removed: int = 0
    gaps: list[dict[str, Any]] = field(default_factory=list)


def _expected_step_seconds(timeframe: str) -> int:
    return TIMEFRAME_DELTA_SECONDS.get(timeframe, 0)


def _pick_front_contract_for_ts(
    ts: datetime,
    candidates: list[Bar],
    fallback_maturity: dict[tuple[str, str], datetime | None],
) -> Bar:
    """Pick the contract whose maturity is the soonest >= ts; fall back to the
    bar whose maturity is closest in absolute time, then to the first bar.
    """
    if len(candidates) == 1:
        return candidates[0]

    def maturity_for(b: Bar) -> datetime | None:
        return fallback_maturity.get((b.source_ticker, b.source_class_code))

    future = [(maturity_for(b), b) for b in candidates if maturity_for(b) is not None and maturity_for(b) >= ts]  # type: ignore[operator]
    if future:
        future.sort(key=lambda pair: pair[0])  # type: ignore[arg-type]
        return future[0][1]

    with_mat = [(maturity_for(b), b) for b in candidates if maturity_for(b) is not None]
    if with_mat:
        with_mat.sort(key=lambda pair: abs((pair[0] - ts).total_seconds()))  # type: ignore[operator]
        return with_mat[0][1]

    # All missing maturities — prefer the later source ticker alphabetically as
    # a stable tie-break (BCS futures tickers tend to embed the month code).
    candidates.sort(key=lambda b: b.source_ticker, reverse=True)
    return candidates[0]


def stitch(
    contracts: list[ContractInfo],
    timeframe: str,
) -> tuple[list[Bar], StitchStats]:
    """Build an unadjusted continuous series across the given contracts."""
    stats = StitchStats()

    # Order by maturity ascending; contracts with no maturity go last so they
    # don't contaminate the front of the series.
    ordered = sorted(
        contracts,
        key=lambda c: (c.maturity is None, c.maturity or datetime(2999, 12, 31, tzinfo=timezone.utc)),
    )

    bars_by_ts: dict[datetime, list[Bar]] = {}
    fallback_maturity: dict[tuple[str, str], datetime | None] = {}

    for contract in ordered:
        path = RAW_DIR / f"{contract.ticker}_{contract.classCode}_{timeframe}.csv"
        contract_bars = load_bars(path, contract.ticker, contract.classCode)
        if not contract_bars:
            stats.contracts_used.append(
                {
                    "ticker": contract.ticker,
                    "classCode": contract.classCode,
                    "maturity": contract.maturity.isoformat().replace("+00:00", "Z")
                    if contract.maturity else None,
                    "maturity_source": contract.inferred_from,
                    "rows": 0,
                    "path": str(path.relative_to(_REPO_ROOT)),
                    "status": "missing_or_empty",
                }
            )
            continue
        fallback_maturity[(contract.ticker, contract.classCode)] = contract.maturity
        stats.contracts_used.append(
            {
                "ticker": contract.ticker,
                "classCode": contract.classCode,
                "maturity": contract.maturity.isoformat().replace("+00:00", "Z")
                if contract.maturity else None,
                "maturity_source": contract.inferred_from,
                "rows": len(contract_bars),
                "first_bar_time": contract_bars[0].timestamp.isoformat().replace("+00:00", "Z"),
                "last_bar_time": contract_bars[-1].timestamp.isoformat().replace("+00:00", "Z"),
                "path": str(path.relative_to(_REPO_ROOT)),
                "status": "ok",
            }
        )
        for bar in contract_bars:
            bars_by_ts.setdefault(bar.timestamp, []).append(bar)

    # Resolve overlaps and deduplicate.
    merged: list[Bar] = []
    for ts in sorted(bars_by_ts.keys()):
        bucket = bars_by_ts[ts]
        if len(bucket) > 1:
            chosen = _pick_front_contract_for_ts(ts, bucket, fallback_maturity)
            stats.overlaps_removed += len(bucket) - 1
            merged.append(chosen)
        else:
            merged.append(bucket[0])

    # Drop any accidental duplicates (shouldn't happen post-bucket, but be safe).
    deduped: list[Bar] = []
    seen: set[datetime] = set()
    for bar in merged:
        if bar.timestamp in seen:
            stats.duplicates_removed += 1
            continue
        seen.add(bar.timestamp)
        deduped.append(bar)

    # Gap detection.
    step = _expected_step_seconds(timeframe)
    if step > 0 and len(deduped) >= 2:
        for prev, curr in zip(deduped[:-1], deduped[1:]):
            elapsed = (curr.timestamp - prev.timestamp).total_seconds()
            if elapsed > step * 5:  # Worth flagging — more than 5 expected steps.
                stats.gaps.append(
                    {
                        "from": prev.timestamp.isoformat().replace("+00:00", "Z"),
                        "to": curr.timestamp.isoformat().replace("+00:00", "Z"),
                        "expected_step_seconds": step,
                        "actual_gap_seconds": int(elapsed),
                    }
                )

    return deduped, stats


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_continuous_csv(path: Path, bars: list[Bar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["timestamp", "open", "high", "low", "close", "volume", "source_ticker", "source_class_code"]
        )
        for b in bars:
            writer.writerow(
                [
                    b.timestamp.isoformat().replace("+00:00", "Z"),
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                    b.source_ticker,
                    b.source_class_code,
                ]
            )


def _max_gap_seconds(gaps: list[dict[str, Any]]) -> int:
    if not gaps:
        return 0
    return max(int(g.get("actual_gap_seconds", 0)) for g in gaps)


def build_summary(
    timeframe_results: dict[str, tuple[list[Bar], StitchStats]],
    contracts: list[ContractInfo],
) -> dict[str, Any]:
    timeframes_payload: dict[str, Any] = {}
    for tf, (bars, stats) in timeframe_results.items():
        timeframes_payload[tf] = {
            "rows": len(bars),
            "first_bar_time": bars[0].timestamp.isoformat().replace("+00:00", "Z") if bars else None,
            "last_bar_time": bars[-1].timestamp.isoformat().replace("+00:00", "Z") if bars else None,
            "contracts_used": stats.contracts_used,
            "overlaps_removed": stats.overlaps_removed,
            "duplicates_removed": stats.duplicates_removed,
            "gaps_count": len(stats.gaps),
            "max_gap_seconds": _max_gap_seconds(stats.gaps),
            "gaps": stats.gaps[:50],
            "warnings": [WARNING_TEXT],
            "output_csv": str(
                (PROCESSED_DIR / f"GOLD_FUT_CONTINUOUS_{tf}.csv").relative_to(_REPO_ROOT)
            ),
        }
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "method": "unadjusted_concatenation",
        "warning": WARNING_TEXT,
        "candidate_contracts": [
            {
                "ticker": c.ticker,
                "classCode": c.classCode,
                "maturity": c.maturity.isoformat().replace("+00:00", "Z") if c.maturity else None,
                "maturity_source": c.inferred_from,
            }
            for c in contracts
        ],
        "timeframes": timeframes_payload,
    }


def write_summary(payload: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "gold_futures_continuous_summary.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stitch downloaded gold futures contracts into a continuous series.")
    p.add_argument(
        "--timeframes",
        nargs="+",
        default=["M5", "M15", "H1"],
        choices=list(TIMEFRAME_DELTA_SECONDS.keys()),
        help="Timeframes to build (default: M5 M15 H1).",
    )
    p.add_argument(
        "--discovery",
        default=str(DISCOVERY_PATH),
        help="Path to the discovery JSON file.",
    )
    p.add_argument(
        "--min-rows",
        type=int,
        default=200,
        help="Minimum rows required to write a continuous CSV (default: 200).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)

    try:
        contracts = load_candidates(Path(args.discovery))
    except (FileNotFoundError, ValueError) as exc:
        print(f"[build-continuous] cannot load discovery: {exc}", file=sys.stderr)
        return 4

    if not contracts:
        print("[build-continuous] no candidate contracts found in discovery.", file=sys.stderr)
        return 5

    timeframe_results: dict[str, tuple[list[Bar], StitchStats]] = {}
    written: list[Path] = []
    for tf in args.timeframes:
        print(f"[build-continuous] timeframe={tf} contracts={len(contracts)}")
        bars, stats = stitch(contracts, tf)
        timeframe_results[tf] = (bars, stats)
        out_path = PROCESSED_DIR / f"GOLD_FUT_CONTINUOUS_{tf}.csv"
        if len(bars) < args.min_rows:
            print(
                f"  -> only {len(bars)} bars (< min-rows={args.min_rows}); "
                f"skipping CSV write."
            )
            continue
        write_continuous_csv(out_path, bars)
        written.append(out_path)
        first_ts = bars[0].timestamp.isoformat().replace("+00:00", "Z")
        last_ts = bars[-1].timestamp.isoformat().replace("+00:00", "Z")
        print(
            f"  -> {len(bars)} bars saved to {out_path.relative_to(_REPO_ROOT)} "
            f"(range {first_ts} .. {last_ts}, overlaps_removed={stats.overlaps_removed}, gaps={len(stats.gaps)})"
        )

    summary_path = write_summary(build_summary(timeframe_results, contracts))
    print(f"[build-continuous] summary: {summary_path.relative_to(_REPO_ROOT)}")
    print(f"[build-continuous] WARNING: {WARNING_TEXT}")
    return 0 if written else 6


if __name__ == "__main__":
    raise SystemExit(main())
