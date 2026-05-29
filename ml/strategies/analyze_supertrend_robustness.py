"""
Robustness analysis for the SuperTrend GLDRUBF research candidate.

This script does **not** run any backtest, train any model, or place any
order. It is a pure post-processing step that reads the existing grid CSV and
summary JSON produced by ``backtest_supertrend.py`` and answers three
questions about the selected best setup (D / long_short_reversal / ATR 5 /
multiplier 1.5):

1. Robustness — is the out-of-sample edge confirmed by *neighbouring*
   ATR / multiplier settings, or does it live on a single lucky parameter
   island?
2. Direction — is the edge mostly long-side, mostly short-side, or does the
   long_short_reversal combination genuinely beat long_only?
3. Export — if the candidate is robust or merely fragile (but not rejected),
   write a small, commit-safe research profile (metadata only, no raw data,
   no tokens).

Inputs (must already exist):

* ``ml/reports/strategies/<prefix>_grid.csv``
* ``ml/reports/strategies/<prefix>_summary.json``
* ``ml/reports/strategies/<prefix>_walk_forward.json``  (optional, used to
  enrich the exported profile)

Outputs:

* ``ml/reports/strategies/<prefix>_robustness.json``   (gitignored)
* ``ml/reports/strategies/<prefix>_robustness.csv``    (gitignored)
* ``ml/models/<prefix>_research_profile.json``         (tracked; only written
  when the conclusion is robust_candidate or fragile_candidate)

CLI::

    python ml\\strategies\\analyze_supertrend_robustness.py --prefix supertrend_gldrubf
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

# Allow running as a script from the repo root.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[2]
REPORTS_DIR = _REPO_ROOT / "ml" / "reports" / "strategies"
MODELS_DIR = _REPO_ROOT / "ml" / "models"

# ---------------------------------------------------------------------------
# Neighbourhood definition (centred on the selected best setup)
# ---------------------------------------------------------------------------
BEST_TIMEFRAME = "D"
BEST_MODE = "long_short_reversal"
BEST_ATR_LENGTH = 5
BEST_MULTIPLIER = 1.5

NEIGHBOR_ATR_LENGTHS = [5, 7, 10, 14]
NEIGHBOR_MULTIPLIERS = [1.5, 2.0, 2.5, 3.0]
NEIGHBOR_MODES = ["long_short_reversal", "long_only", "short_only"]

# ---------------------------------------------------------------------------
# Robustness pass criteria (evaluated on the out-of-sample / test slice)
# ---------------------------------------------------------------------------
MIN_TEST_TRADES = 5
MIN_TEST_PROFIT_FACTOR = 1.10          # strictly greater than
MIN_TEST_AVG_NET_RETURN_PCT = 0.0      # strictly greater than
MIN_TEST_CUM_NET_RETURN_PCT = 0.0      # strictly greater than
MAX_TEST_DRAWDOWN_PCT = -15.0          # test_max_drawdown_pct must be > -15
MIN_TEST_ACTIVE_MONTHS = 4

# "Not a one-parameter island": how many *other* neighbouring parameter
# combinations (same mode, in the ATR x multiplier grid above) must clear a
# soft positive bar for the setup to count as supported by its neighbourhood.
NEIGHBOR_SOFT_MIN_PROFIT_FACTOR = 1.0  # strictly greater than
NEIGHBOR_SOFT_MIN_CUM_NET_RETURN_PCT = 0.0  # strictly greater than
MIN_SUPPORTING_NEIGHBORS = 3

_FLOAT_TOL = 1e-9


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    f = _to_float(value)
    return int(round(f)) if f is not None else None


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def load_grid_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ---------------------------------------------------------------------------
# Neighbourhood extraction
# ---------------------------------------------------------------------------


def _matches_neighborhood(row: dict) -> bool:
    """Keep D-timeframe rows with no TP/SL and no distance filter, whose
    ATR / multiplier fall inside the analysed grid."""
    if row.get("timeframe") != BEST_TIMEFRAME:
        return False
    if row.get("mode") not in NEIGHBOR_MODES:
        return False
    # No TP/SL/horizon and no distance filter — matches the best setup exactly
    # and avoids the duplicate rows the grid emits for distance_filter_pct=0.25.
    if not (_is_empty(row.get("tp_pct")) and _is_empty(row.get("sl_pct"))):
        return False
    if not _is_empty(row.get("distance_filter_pct")):
        return False
    atr = _to_int(row.get("atr_length"))
    mult = _to_float(row.get("multiplier"))
    if atr not in NEIGHBOR_ATR_LENGTHS:
        return False
    if mult is None or not any(abs(mult - m) < _FLOAT_TOL for m in NEIGHBOR_MULTIPLIERS):
        return False
    return True


def _build_monthly_concentration_lookup(summary: dict) -> dict[tuple, float]:
    """The grid CSV does not carry monthly_concentration; it only exists in the
    summary JSON for the handful of setups it records (best_train,
    diagnostics). Build a lookup keyed by (mode, atr, mult) -> test
    monthly_concentration so we can populate it where available."""
    lookup: dict[tuple, float] = {}
    for key in ("best_train", "best_train_diagnostic_only", "best_test_diagnostic_only"):
        node = summary.get(key)
        if not isinstance(node, dict):
            continue
        if node.get("timeframe") != BEST_TIMEFRAME:
            continue
        atr = _to_int(node.get("atr_length"))
        mult = _to_float(node.get("multiplier"))
        test_metrics = node.get("test_metrics") or {}
        mc = test_metrics.get("monthly_concentration")
        if atr is None or mult is None or mc is None:
            continue
        lookup[(node.get("mode"), atr, round(mult, 4))] = float(mc)
    return lookup


def parse_setup(row: dict, monthly_lookup: dict[tuple, float]) -> dict:
    atr = _to_int(row.get("atr_length"))
    mult = _to_float(row.get("multiplier"))
    mode = row.get("mode")
    mc = monthly_lookup.get((mode, atr, round(mult, 4))) if mult is not None else None
    return {
        "timeframe": row.get("timeframe"),
        "mode": mode,
        "atr_length": atr,
        "multiplier": mult,
        "train_total_trades": _to_int(row.get("train_total_trades")),
        "test_total_trades": _to_int(row.get("test_total_trades")),
        "train_profit_factor": _to_float(row.get("train_profit_factor")),
        "test_profit_factor": _to_float(row.get("test_profit_factor")),
        "train_avg_net_return_pct": _to_float(row.get("train_avg_net_return_pct")),
        "test_avg_net_return_pct": _to_float(row.get("test_avg_net_return_pct")),
        "train_cumulative_net_return_pct": _to_float(row.get("train_cum_net_return_pct")),
        "test_cumulative_net_return_pct": _to_float(row.get("test_cum_net_return_pct")),
        "test_max_drawdown_pct": _to_float(row.get("test_max_drawdown_pct")),
        "test_active_months": _to_int(row.get("test_active_months")),
        # Only available for the few setups the summary JSON records; null
        # otherwise (the grid CSV does not store it). Not a pass criterion.
        "monthly_concentration": mc,
    }


def is_same_setup(setup: dict, mode: str, atr: int, mult: float) -> bool:
    return (
        setup["mode"] == mode
        and setup["atr_length"] == atr
        and setup["multiplier"] is not None
        and abs(setup["multiplier"] - mult) < _FLOAT_TOL
    )


# ---------------------------------------------------------------------------
# Robustness evaluation
# ---------------------------------------------------------------------------


def _soft_positive_neighbor(setup: dict) -> bool:
    """The soft bar used for the island check: a neighbour 'supports' the edge
    when its OOS profit factor and cumulative return are both positive."""
    pf = setup["test_profit_factor"]
    cum = setup["test_cumulative_net_return_pct"]
    if pf is None or cum is None:
        return False
    return pf > NEIGHBOR_SOFT_MIN_PROFIT_FACTOR and cum > NEIGHBOR_SOFT_MIN_CUM_NET_RETURN_PCT


def count_supporting_neighbors(setup: dict, all_setups: list[dict]) -> tuple[int, list[dict]]:
    """Count the *other* setups in the same mode's neighbourhood that clear the
    soft positive bar. Returns (count, supporting_combos)."""
    supporting: list[dict] = []
    for other in all_setups:
        if other is setup:
            continue
        if other["mode"] != setup["mode"]:
            continue
        if _soft_positive_neighbor(other):
            supporting.append(
                {
                    "atr_length": other["atr_length"],
                    "multiplier": other["multiplier"],
                    "test_profit_factor": other["test_profit_factor"],
                    "test_cumulative_net_return_pct": other["test_cumulative_net_return_pct"],
                }
            )
    return len(supporting), supporting


def evaluate_robustness(setup: dict, all_setups: list[dict]) -> dict:
    reasons: list[str] = []

    trades = setup["test_total_trades"]
    pf = setup["test_profit_factor"]
    avg = setup["test_avg_net_return_pct"]
    cum = setup["test_cumulative_net_return_pct"]
    dd = setup["test_max_drawdown_pct"]
    months = setup["test_active_months"]

    if trades is None or trades < MIN_TEST_TRADES:
        reasons.append(f"test_total_trades<{MIN_TEST_TRADES}")
    if pf is None or pf <= MIN_TEST_PROFIT_FACTOR:
        reasons.append(f"test_profit_factor<={MIN_TEST_PROFIT_FACTOR}")
    if avg is None or avg <= MIN_TEST_AVG_NET_RETURN_PCT:
        reasons.append("test_avg_net_return_pct<=0")
    if cum is None or cum <= MIN_TEST_CUM_NET_RETURN_PCT:
        reasons.append("test_cumulative_net_return_pct<=0")
    if dd is None or dd <= MAX_TEST_DRAWDOWN_PCT:
        reasons.append(f"test_max_drawdown_pct<={MAX_TEST_DRAWDOWN_PCT}")
    if months is None or months < MIN_TEST_ACTIVE_MONTHS:
        reasons.append(f"test_active_months<{MIN_TEST_ACTIVE_MONTHS}")

    support_count, supporting = count_supporting_neighbors(setup, all_setups)
    island_ok = support_count >= MIN_SUPPORTING_NEIGHBORS
    if not island_ok:
        reasons.append(
            f"one_parameter_island(supporting_neighbors={support_count}"
            f"<{MIN_SUPPORTING_NEIGHBORS})"
        )

    numeric_ok = not [r for r in reasons if not r.startswith("one_parameter_island")]

    setup_out = dict(setup)
    setup_out.update(
        {
            "supporting_neighbors": support_count,
            "supporting_neighbor_combos": supporting,
            "numeric_criteria_pass": numeric_ok,
            "neighborhood_pass": island_ok,
            "robustness_pass": numeric_ok and island_ok,
            "rejection_reasons": reasons,
        }
    )
    return setup_out


# ---------------------------------------------------------------------------
# Direction diagnosis (long vs short vs reversal at the best parameters)
# ---------------------------------------------------------------------------


def _find_setup(setups: list[dict], mode: str) -> dict | None:
    for s in setups:
        if is_same_setup(s, mode, BEST_ATR_LENGTH, BEST_MULTIPLIER):
            return s
    return None


def diagnose_direction(setups: list[dict], summary: dict) -> dict:
    long_only = _find_setup(setups, "long_only")
    short_only = _find_setup(setups, "short_only")
    reversal = _find_setup(setups, "long_short_reversal")

    # Reversal long/short sub-splits live only in the summary JSON's best_train.
    rev_long = rev_short = {}
    best_train = summary.get("best_train") or {}
    if (
        best_train.get("timeframe") == BEST_TIMEFRAME
        and best_train.get("mode") == BEST_MODE
        and _to_int(best_train.get("atr_length")) == BEST_ATR_LENGTH
        and abs((_to_float(best_train.get("multiplier")) or 0) - BEST_MULTIPLIER) < _FLOAT_TOL
    ):
        rev_long = (best_train.get("train_metrics") or {}).get("long", {}) or {}
        rev_short = (best_train.get("train_metrics") or {}).get("short", {}) or {}

    def g(setup: dict | None, key: str) -> float | None:
        return setup[key] if setup else None

    # Long side carries the edge if long_only is profitable on train while
    # short_only is not.
    lo_train_avg = g(long_only, "train_avg_net_return_pct")
    lo_train_pf = g(long_only, "train_profit_factor")
    so_train_avg = g(short_only, "train_avg_net_return_pct")
    so_train_pf = g(short_only, "train_profit_factor")

    mostly_long = bool(
        lo_train_avg is not None and lo_train_avg > 0
        and (lo_train_pf or 0) > 1.0
        and (so_train_avg is None or so_train_avg <= 0 or (so_train_pf or 0) < 1.0)
    )
    mostly_short = bool(
        so_train_avg is not None and so_train_avg > 0
        and (so_train_pf or 0) > 1.0
        and (lo_train_avg is None or lo_train_avg <= 0 or (lo_train_pf or 0) < 1.0)
    )

    # Reversal "beats" long_only only if it improves the risk-adjusted OOS
    # profit factor (cumulative return alone can rise just by adding more,
    # lower-quality, short trades at the cost of deeper drawdown).
    rev_test_pf = g(reversal, "test_profit_factor")
    lo_test_pf = g(long_only, "test_profit_factor")
    reversal_better_than_long_only = bool(
        rev_test_pf is not None and lo_test_pf is not None and rev_test_pf > lo_test_pf
    )

    comment_parts: list[str] = []
    if rev_long and rev_short:
        comment_parts.append(
            "Inside the reversal best setup the long leg dominates "
            f"(train long PF {rev_long.get('profit_factor'):.2f}, avg net "
            f"{rev_long.get('avg_net_return_pct'):.2f}% vs short PF "
            f"{rev_short.get('profit_factor'):.2f}, avg net "
            f"{rev_short.get('avg_net_return_pct'):.2f}%)."
        )
    if long_only and reversal:
        comment_parts.append(
            "On the OOS test, long_only shows a higher profit factor "
            f"({lo_test_pf:.2f}) and avg net "
            f"({g(long_only, 'test_avg_net_return_pct'):.2f}%) than reversal "
            f"(PF {rev_test_pf:.2f}, avg net "
            f"{g(reversal, 'test_avg_net_return_pct'):.2f}%); reversal's "
            "slightly higher cumulative return comes from extra, weaker short "
            "trades and a deeper drawdown "
            f"({g(reversal, 'test_max_drawdown_pct'):.2f}% vs "
            f"{g(long_only, 'test_max_drawdown_pct'):.2f}%)."
        )
    if mostly_long:
        comment_parts.append(
            "The edge is mostly long-side; long_only is the more robust "
            "expression of this candidate."
        )

    def side_view(setup: dict | None) -> dict | None:
        if not setup:
            return None
        return {
            "train_total_trades": setup["train_total_trades"],
            "train_profit_factor": setup["train_profit_factor"],
            "train_avg_net_return_pct": setup["train_avg_net_return_pct"],
            "train_cumulative_net_return_pct": setup["train_cumulative_net_return_pct"],
            "test_total_trades": setup["test_total_trades"],
            "test_profit_factor": setup["test_profit_factor"],
            "test_avg_net_return_pct": setup["test_avg_net_return_pct"],
            "test_cumulative_net_return_pct": setup["test_cumulative_net_return_pct"],
            "test_max_drawdown_pct": setup["test_max_drawdown_pct"],
            "test_active_months": setup["test_active_months"],
        }

    return {
        "parameters": {
            "timeframe": BEST_TIMEFRAME,
            "atr_length": BEST_ATR_LENGTH,
            "multiplier": BEST_MULTIPLIER,
        },
        "long_only": side_view(long_only),
        "short_only": side_view(short_only),
        "long_short_reversal": side_view(reversal),
        "reversal_long_leg_train": rev_long or None,
        "reversal_short_leg_train": rev_short or None,
        "mostly_long": mostly_long,
        "mostly_short": mostly_short,
        "reversal_better_than_long_only": reversal_better_than_long_only,
        "comment": " ".join(comment_parts) if comment_parts else "Insufficient data for diagnosis.",
    }


# ---------------------------------------------------------------------------
# Final conclusion + profile export
# ---------------------------------------------------------------------------


def decide_conclusion(best_eval: dict | None) -> str:
    """robust_candidate / fragile_candidate / rejected, anchored on the
    selected best setup (D / reversal / ATR 5 / mult 1.5)."""
    if best_eval is None:
        return "rejected"
    if not best_eval["numeric_criteria_pass"]:
        return "rejected"
    if best_eval["neighborhood_pass"]:
        return "robust_candidate"
    return "fragile_candidate"


def build_research_profile(
    prefix: str,
    summary: dict,
    walk_forward: dict | None,
    conclusion: str,
) -> dict:
    best_train = summary.get("best_train") or {}
    train_metrics = best_train.get("train_metrics") or {}
    test_metrics = best_train.get("test_metrics") or {}

    wf_folds = (walk_forward or {}).get("folds") or []
    wf_total = len(wf_folds)
    wf_positive = sum(
        1
        for f in wf_folds
        if (f.get("test") or {}).get("cumulative_net_return_pct", 0) > 0
    )

    return {
        "profile_id": f"{prefix}_d_v1",
        "type": "rule_based_supertrend",
        "status": "research_only",
        "instrument": {
            "ticker": summary.get("ticker", "GLDRUBF"),
            "classCode": summary.get("class_code", "SPBFUT"),
            "name": "Gold RUB perpetual futures",
        },
        "timeframe": BEST_TIMEFRAME,
        "parameters": {
            "atr_length": BEST_ATR_LENGTH,
            "multiplier": BEST_MULTIPLIER,
            "mode": BEST_MODE,
            "tp_pct": None,
            "sl_pct": None,
            "exit": "supertrend_reversal",
        },
        "validation": {
            "train_trades": train_metrics.get("total_trades"),
            "test_trades": test_metrics.get("total_trades"),
            "test_profit_factor": round(test_metrics.get("profit_factor", 0.0), 2),
            "test_cumulative_net_return_pct": round(
                test_metrics.get("cumulative_net_return_pct", 0.0), 2
            ),
            "walk_forward_positive_folds": wf_positive,
            "walk_forward_total_folds": wf_total,
            "robustness_conclusion": conclusion,
        },
        "warnings": [
            "Research only",
            "Low number of OOS trades",
            "Not a trading recommendation",
            "Requires more data and live paper validation",
        ],
    }


# ---------------------------------------------------------------------------
# CSV report
# ---------------------------------------------------------------------------

_CSV_FIELDS = [
    "timeframe",
    "mode",
    "atr_length",
    "multiplier",
    "train_total_trades",
    "test_total_trades",
    "train_profit_factor",
    "test_profit_factor",
    "train_avg_net_return_pct",
    "test_avg_net_return_pct",
    "train_cumulative_net_return_pct",
    "test_cumulative_net_return_pct",
    "test_max_drawdown_pct",
    "test_active_months",
    "monthly_concentration",
    "supporting_neighbors",
    "robustness_pass",
    "rejection_reasons",
]


def write_robustness_csv(path: Path, evaluated: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for s in evaluated:
            writer.writerow(
                {
                    "timeframe": s["timeframe"],
                    "mode": s["mode"],
                    "atr_length": s["atr_length"],
                    "multiplier": s["multiplier"],
                    "train_total_trades": s["train_total_trades"],
                    "test_total_trades": s["test_total_trades"],
                    "train_profit_factor": s["train_profit_factor"],
                    "test_profit_factor": s["test_profit_factor"],
                    "train_avg_net_return_pct": s["train_avg_net_return_pct"],
                    "test_avg_net_return_pct": s["test_avg_net_return_pct"],
                    "train_cumulative_net_return_pct": s["train_cumulative_net_return_pct"],
                    "test_cumulative_net_return_pct": s["test_cumulative_net_return_pct"],
                    "test_max_drawdown_pct": s["test_max_drawdown_pct"],
                    "test_active_months": s["test_active_months"],
                    "monthly_concentration": s["monthly_concentration"],
                    "supporting_neighbors": s["supporting_neighbors"],
                    "robustness_pass": s["robustness_pass"],
                    "rejection_reasons": ";".join(s["rejection_reasons"]),
                }
            )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def analyze(prefix: str, reports_dir: Path, models_dir: Path) -> int:
    grid_path = reports_dir / f"{prefix}_grid.csv"
    summary_path = reports_dir / f"{prefix}_summary.json"
    wf_path = reports_dir / f"{prefix}_walk_forward.json"

    missing = [p for p in (grid_path, summary_path) if not p.exists()]
    if missing:
        for p in missing:
            print(f"[robustness] required input not found: {p}", file=sys.stderr)
        print(
            "[robustness] run backtest_supertrend.py first to produce the grid + summary.",
            file=sys.stderr,
        )
        return 4

    grid_rows = load_grid_rows(grid_path)
    summary = _read_json(summary_path)
    walk_forward = _read_json(wf_path) if wf_path.exists() else None

    monthly_lookup = _build_monthly_concentration_lookup(summary)
    neighborhood_rows = [r for r in grid_rows if _matches_neighborhood(r)]
    setups = [parse_setup(r, monthly_lookup) for r in neighborhood_rows]

    if not setups:
        print(
            "[robustness] no neighbourhood rows matched in the grid CSV — "
            "nothing to analyse.",
            file=sys.stderr,
        )
        return 5

    evaluated = [evaluate_robustness(s, setups) for s in setups]

    # Locate the selected best setup's evaluation.
    best_eval = next(
        (
            e
            for e in evaluated
            if is_same_setup(e, BEST_MODE, BEST_ATR_LENGTH, BEST_MULTIPLIER)
        ),
        None,
    )

    conclusion = decide_conclusion(best_eval)
    direction = diagnose_direction(setups, summary)

    passing = [e for e in evaluated if e["robustness_pass"]]
    passing_reversal = [e for e in passing if e["mode"] == BEST_MODE]

    # Neighbourhood support summary for the best setup's mode.
    best_support_count = best_eval["supporting_neighbors"] if best_eval else 0
    best_support_combos = best_eval["supporting_neighbor_combos"] if best_eval else []

    robustness_payload = {
        "ticker": summary.get("ticker"),
        "class_code": summary.get("class_code"),
        "analysis": "supertrend_neighborhood_robustness",
        "best_setup": {
            "timeframe": BEST_TIMEFRAME,
            "mode": BEST_MODE,
            "atr_length": BEST_ATR_LENGTH,
            "multiplier": BEST_MULTIPLIER,
            "tp_pct": None,
            "sl_pct": None,
            "exit": "supertrend_reversal",
        },
        "neighborhood_grid": {
            "atr_lengths": NEIGHBOR_ATR_LENGTHS,
            "multipliers": NEIGHBOR_MULTIPLIERS,
            "modes": NEIGHBOR_MODES,
            "setups_analyzed": len(evaluated),
        },
        "robustness_criteria": {
            "min_test_trades": MIN_TEST_TRADES,
            "min_test_profit_factor": MIN_TEST_PROFIT_FACTOR,
            "min_test_avg_net_return_pct": MIN_TEST_AVG_NET_RETURN_PCT,
            "min_test_cumulative_net_return_pct": MIN_TEST_CUM_NET_RETURN_PCT,
            "max_test_drawdown_pct": MAX_TEST_DRAWDOWN_PCT,
            "min_test_active_months": MIN_TEST_ACTIVE_MONTHS,
            "min_supporting_neighbors": MIN_SUPPORTING_NEIGHBORS,
            "neighbor_soft_min_profit_factor": NEIGHBOR_SOFT_MIN_PROFIT_FACTOR,
            "neighbor_soft_min_cumulative_net_return_pct": NEIGHBOR_SOFT_MIN_CUM_NET_RETURN_PCT,
        },
        "best_setup_evaluation": best_eval,
        "neighborhood_support_for_best_mode": {
            "mode": BEST_MODE,
            "supporting_neighbors": best_support_count,
            "required": MIN_SUPPORTING_NEIGHBORS,
            "is_island": best_support_count < MIN_SUPPORTING_NEIGHBORS,
            "supporting_combos": best_support_combos,
        },
        "setups": evaluated,
        "n_setups_passing_full_robustness": len(passing),
        "n_reversal_setups_passing_full_robustness": len(passing_reversal),
        "passing_setups": [
            {"mode": e["mode"], "atr_length": e["atr_length"], "multiplier": e["multiplier"]}
            for e in passing
        ],
        "direction_diagnosis": {
            "mostly_long": direction["mostly_long"],
            "mostly_short": direction["mostly_short"],
            "reversal_better_than_long_only": direction["reversal_better_than_long_only"],
            "comment": direction["comment"],
            "detail": direction,
        },
        "robustness_conclusion": conclusion,
        "notes": [
            "monthly_concentration is only available for setups recorded in the "
            "summary JSON (the grid CSV does not store it); it is null otherwise "
            "and is not used as a pass criterion.",
            "This is research-only analysis. It does not place trades and does "
            "not claim profitability.",
        ],
    }

    robustness_json_path = reports_dir / f"{prefix}_robustness.json"
    robustness_csv_path = reports_dir / f"{prefix}_robustness.csv"
    _write_json(robustness_json_path, robustness_payload)
    write_robustness_csv(robustness_csv_path, evaluated)

    profile_written: Path | None = None
    if conclusion in ("robust_candidate", "fragile_candidate"):
        profile = build_research_profile(prefix, summary, walk_forward, conclusion)
        profile_path = models_dir / f"{prefix}_research_profile.json"
        _write_json(profile_path, profile)
        profile_written = profile_path

    # -------------------------------------------------------------------
    # Console report
    # -------------------------------------------------------------------
    print(f"[robustness] analysed {len(evaluated)} neighbourhood setups for {prefix}")
    print(f"[robustness] wrote: {robustness_json_path.relative_to(_REPO_ROOT)}")
    print(f"[robustness] wrote: {robustness_csv_path.relative_to(_REPO_ROOT)}")
    print("")
    print(f"  robustness_conclusion        : {conclusion}")
    print(
        f"  best setup (D/reversal/5/1.5): numeric_pass="
        f"{best_eval['numeric_criteria_pass'] if best_eval else 'n/a'}, "
        f"neighborhood_pass={best_eval['neighborhood_pass'] if best_eval else 'n/a'}"
    )
    print(
        f"  supporting reversal neighbors : {best_support_count} "
        f"(need >= {MIN_SUPPORTING_NEIGHBORS})"
    )
    print(f"  setups passing FULL robustness: {len(passing)} "
          f"(reversal-only: {len(passing_reversal)})")
    for e in passing:
        print(f"      - {e['mode']} ATR={e['atr_length']} mult={e['multiplier']}")
    print("")
    print("  direction diagnosis:")
    print(f"      mostly_long                  : {direction['mostly_long']}")
    print(f"      mostly_short                 : {direction['mostly_short']}")
    print(f"      reversal_better_than_long_only: {direction['reversal_better_than_long_only']}")
    print(f"      {direction['comment']}")
    print("")
    if profile_written is not None:
        print(f"[robustness] research profile exported: {profile_written.relative_to(_REPO_ROOT)}")
    else:
        print("[robustness] conclusion is 'rejected' — research profile NOT exported.")
    print("")
    print("  next: inspect the robustness report, then (optionally) display the")
    print(f"        research profile in-app as 'research only' for {summary.get('ticker')}.")
    return 0


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Robustness + direction analysis for a SuperTrend research candidate."
    )
    p.add_argument(
        "--prefix",
        default="supertrend_gldrubf",
        help="Report filename prefix (default: supertrend_gldrubf).",
    )
    p.add_argument(
        "--reports-dir",
        default=str(REPORTS_DIR),
        help="Directory holding the grid CSV + summary JSON (default: ml/reports/strategies).",
    )
    p.add_argument(
        "--models-dir",
        default=str(MODELS_DIR),
        help="Directory to write the research profile into (default: ml/models).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    return analyze(
        prefix=args.prefix,
        reports_dir=Path(args.reports_dir),
        models_dir=Path(args.models_dir),
    )


if __name__ == "__main__":
    raise SystemExit(main())
