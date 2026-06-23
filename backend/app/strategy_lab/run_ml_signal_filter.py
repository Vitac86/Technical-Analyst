"""Strategy Lab v1.5 runner: ML signal dataset + CatBoost signal filter.

This orchestrates the leakage-safe pipeline in :mod:`ml_signal_filter`:

    1. build (or reuse) the ML dataset of executed rule-based trade candidates,
    2. time-split it into train / validation / test by ``signal_time``,
    3. train a CatBoost classifier per (finalist, cost_scenario) on the train
       period to predict ``y_good_trade``,
    4. pick a probability threshold on the *validation* period only,
    5. apply it to the held-out test period and compare filtered vs unfiltered
       trades, plus a fixed walk-forward breakdown.

It writes research CSVs + models under a git-ignored reports folder and prints a
console summary. It is research only -- it never trades, adds no UI, and never
modifies the MT5 exports.

Run directly::

    python backend/app/strategy_lab/run_ml_signal_filter.py

or as a module from ``backend``::

    python -m app.strategy_lab.run_ml_signal_filter
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:  # package import
    from . import ml_features, ml_signal_filter
except ImportError:  # plain-script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ml_features  # type: ignore[no-redef]
    import ml_signal_filter  # type: ignore[no-redef]

# run_ml_signal_filter.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "MetaTrader_Data" / "mt5_exports"
OUTPUT_DIR = REPO_ROOT / "MetaTrader_Data" / "reports" / "ml_signal_filter_v1_5"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strategy Lab v1.5 ML signal filter (CatBoost)"
    )
    parser.add_argument("--finalist", choices=["D", "C", "all"], default="all")
    parser.add_argument(
        "--cost-scenario",
        choices=["Base", "Conservative", "Stress", "all"],
        default="all",
    )
    parser.add_argument("--leverage", type=float, default=ml_signal_filter.DEFAULT_LEVERAGE)
    parser.add_argument(
        "--target",
        choices=["y_good_trade", "y_profitable"],
        default="y_good_trade",
        help="Primary classification target (default y_good_trade).",
    )
    parser.add_argument(
        "--max-configs",
        type=int,
        default=None,
        help="Safety cap on the number of rule-based configs per finalist.",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--skip-training",
        action="store_true",
        help="Only build (and save) the dataset; do not train CatBoost.",
    )
    parser.add_argument(
        "--reuse-dataset",
        type=Path,
        default=None,
        help="Reuse an existing ml_signal_dataset.csv instead of rebuilding it.",
    )
    return parser.parse_args(argv)


def _resolve_finalists(arg: str) -> list[str]:
    return ["D", "C"] if arg == "all" else [arg]


def _resolve_cost_scenarios(arg: str) -> list[str]:
    return list(ml_signal_filter.COST_SCENARIO_ORDER) if arg == "all" else [arg]


# ---------------------------------------------------------------------------
# Dataset acquisition
# ---------------------------------------------------------------------------

def _load_or_build_dataset(args: argparse.Namespace) -> pd.DataFrame:
    """Reuse a saved dataset or build a fresh one from the MT5 exports."""
    finalists = _resolve_finalists(args.finalist)
    cost_scenarios = _resolve_cost_scenarios(args.cost_scenario)

    if args.reuse_dataset is not None:
        print(f"Reusing dataset: {args.reuse_dataset}")
        dataset = pd.read_csv(
            args.reuse_dataset,
            parse_dates=["signal_time", "entry_time", "exit_time"],
        )
        # Keep only the finalists / cost scenarios requested for this run.
        dataset = dataset[
            dataset["finalist"].isin(finalists)
            & dataset["cost_scenario"].isin(cost_scenarios)
        ].reset_index(drop=True)
        return dataset

    print("Building ML signal dataset from MT5 exports ...")
    return ml_signal_filter.build_dataset(
        DATA_DIR,
        finalists,
        cost_scenarios,
        leverage=args.leverage,
        max_configs=args.max_configs,
    )


def _feature_columns_table(finalists: list[str]) -> pd.DataFrame:
    """Tabular description of which features each finalist's model consumes."""
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for finalist in finalists:
        for col in ml_features.feature_columns_for_finalist(finalist):
            key = (finalist, col)
            if key in seen:
                continue
            seen.add(key)
            if col.endswith("_h4"):
                source = "H4_context"
            elif col.endswith("_d1"):
                source = "D1_context"
            else:
                source = "signal_timeframe"
            rows.append({"finalist": finalist, "feature": col, "source": source})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Training orchestration
# ---------------------------------------------------------------------------

def _train_all(
    dataset: pd.DataFrame,
    finalists: list[str],
    cost_scenarios: list[str],
    *,
    target: str,
    models_dir: Path,
) -> dict[str, pd.DataFrame]:
    """Train per (finalist, cost_scenario) and collect every output table."""
    import joblib  # local import: only needed when training

    threshold_tables: list[pd.DataFrame] = []
    model_rows: list[dict] = []
    filtered_rows: list[dict] = []
    walk_forward_rows: list[dict] = []
    importance_rows: list[dict] = []

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    models_dir.mkdir(parents=True, exist_ok=True)

    for finalist in finalists:
        feature_columns = ml_features.feature_columns_for_finalist(finalist)
        for cost in cost_scenarios:
            frame = dataset[
                (dataset["finalist"] == finalist)
                & (dataset["cost_scenario"] == cost)
            ].copy()
            if frame.empty:
                continue
            # Only use feature columns actually present (HTF merge may add them).
            feat_cols = [c for c in feature_columns if c in frame.columns]

            print(f"\n=== Training finalist {finalist} / {cost} ===")
            trained = ml_signal_filter.train_model(
                frame,
                feat_cols,
                finalist=finalist,
                cost_scenario=cost,
                target=target,
            )
            if trained is None:
                continue

            val = frame[frame["split"] == "validation"]
            test = frame[frame["split"] == "test"]
            val_prob = ml_signal_filter.predict_proba(trained, val)
            choice = ml_signal_filter.search_threshold(
                val, val_prob, finalist=finalist, cost_scenario=cost
            )
            threshold_tables.append(choice.table)

            test_prob = ml_signal_filter.predict_proba(trained, test)
            filtered_rows.append(
                ml_signal_filter.filtered_backtest_row(
                    test,
                    test_prob,
                    finalist=finalist,
                    cost_scenario=cost,
                    threshold=choice.threshold,
                    validation_score=choice.validation_score,
                    test_status=choice.status,
                )
            )
            walk_forward_rows.extend(
                ml_signal_filter.walk_forward_rows(
                    frame, trained, threshold=choice.threshold, test_status=choice.status
                )
            )
            importance_rows.extend(ml_signal_filter.feature_importance_rows(trained))

            model_rows.append(
                {
                    "finalist": finalist,
                    "cost_scenario": cost,
                    "target": target,
                    "train_rows": trained.train_rows,
                    "validation_rows": trained.validation_rows,
                    "test_rows": trained.test_rows,
                    "train_auc": trained.train_auc,
                    "validation_auc": trained.validation_auc,
                    "best_iteration": trained.best_iteration,
                    "chosen_threshold": choice.threshold,
                    "validation_score": choice.validation_score,
                    "threshold_status": choice.status,
                    "n_features": len(feat_cols),
                }
            )

            model_path = models_dir / f"catboost_{finalist}_{cost}_{timestamp}.joblib"
            joblib.dump(
                {
                    "model": trained.model,
                    "feature_columns": trained.feature_columns,
                    "finalist": finalist,
                    "cost_scenario": cost,
                    "target": target,
                    "threshold": choice.threshold,
                    "threshold_status": choice.status,
                },
                model_path,
            )
            print(f"  saved model -> {model_path.name}")

    filtered_summary = pd.DataFrame(filtered_rows)
    return {
        "threshold_search": (
            pd.concat(threshold_tables, ignore_index=True)
            if threshold_tables
            else pd.DataFrame()
        ),
        "model_training_summary": pd.DataFrame(model_rows),
        "filtered_backtest_summary": filtered_summary,
        "walk_forward_ml_summary": pd.DataFrame(walk_forward_rows),
        "feature_importance": pd.DataFrame(importance_rows),
        "top_filtered_candidates": ml_signal_filter.rank_candidates(filtered_summary),
    }


# ---------------------------------------------------------------------------
# Console report (spec section 13)
# ---------------------------------------------------------------------------

def _print_report(
    dataset: pd.DataFrame,
    feature_columns: list[str],
    outputs: dict[str, pd.DataFrame],
    output_dir: Path,
    target: str,
) -> None:
    print("\n" + "=" * 70)
    print("STRATEGY LAB v1.5 -- ML SIGNAL FILTER SUMMARY")
    print("=" * 70)

    print(f"Dataset rows          : {len(dataset)}")
    print(f"Feature count         : {len(feature_columns)}")
    if target in dataset.columns and len(dataset):
        print(
            f"Target positive rate  : {dataset[target].mean():.4f} "
            f"({target})"
        )
    for split in ("train", "validation", "test"):
        n = int((dataset["split"] == split).sum()) if "split" in dataset else 0
        print(f"  {split:<11}rows     : {n}")

    filtered = outputs.get("filtered_backtest_summary", pd.DataFrame())
    if not filtered.empty:
        print("\nBest threshold per finalist / cost scenario:")
        with pd.option_context("display.max_columns", None, "display.width", 240):
            cols = [
                "finalist",
                "cost_scenario",
                "threshold",
                "test_status",
                "original_trades",
                "filtered_trades",
                "original_profit_factor",
                "filtered_profit_factor",
                "original_average_r",
                "filtered_average_r",
            ]
            print(filtered[cols].to_string(index=False))

        print("\nDid the ML filter improve each finalist / cost scenario (test)?")
        for _, r in filtered.iterrows():
            improved = (
                r["filtered_profit_factor"] > r["original_profit_factor"]
                and r["filtered_average_r"] > r["original_average_r"]
            )
            verdict = "IMPROVED" if improved else "no improvement / worse"
            print(
                f"  {r['finalist']}/{r['cost_scenario']:<12} "
                f"PF {r['original_profit_factor']:.2f} -> {r['filtered_profit_factor']:.2f}, "
                f"avgR {r['original_average_r']:.3f} -> {r['filtered_average_r']:.3f}  "
                f"[{verdict}]"
            )

    top = outputs.get("top_filtered_candidates", pd.DataFrame())
    print("\nTop 20 filtered candidates (passed ranking gates):")
    if top is None or top.empty:
        print("  (none passed the ranking gates)")
    else:
        with pd.option_context("display.max_columns", None, "display.width", 240):
            cols = [
                "finalist",
                "cost_scenario",
                "threshold",
                "filtered_trades",
                "filtered_profit_factor",
                "filtered_average_r",
                "filtered_max_drawdown_pct",
                "ml_filter_score",
            ]
            print(top.head(20)[cols].to_string(index=False))

    print(f"\nOutput folder         : {output_dir}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    finalists = _resolve_finalists(args.finalist)
    cost_scenarios = _resolve_cost_scenarios(args.cost_scenario)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    dataset = _load_or_build_dataset(args)
    if dataset.empty:
        print("No dataset rows were produced; nothing to do.")
        return 1

    # Assign the time-based split before validation (checks rely on it).
    dataset["split"] = ml_signal_filter.assign_split(dataset["signal_time"])

    # Feature columns are the union across the finalists actually present.
    present_finalists = [f for f in finalists if (dataset["finalist"] == f).any()]
    feature_columns: list[str] = []
    for finalist in present_finalists:
        for col in ml_features.feature_columns_for_finalist(finalist):
            if col not in feature_columns and col in dataset.columns:
                feature_columns.append(col)

    print("\nRunning dataset validation checks ...")
    for note in ml_signal_filter.validate_dataset(dataset, feature_columns):
        print(f"  [ok] {note}")

    # --- write dataset artefacts ------------------------------------------
    # In reuse mode the dataset already exists on disk; don't clobber it with a
    # (possibly finalist/cost-filtered) subset of itself.
    dataset_path = output_dir / "ml_signal_dataset.csv"
    if args.reuse_dataset is None:
        dataset.to_csv(dataset_path, index=False)
        print(f"\nWrote dataset ({len(dataset)} rows) -> {dataset_path}")
    else:
        print(f"\nReusing dataset on disk ({len(dataset)} rows after filtering).")

    _feature_columns_table(present_finalists).to_csv(
        output_dir / "feature_columns.csv", index=False
    )
    ml_signal_filter.split_summary(dataset).to_csv(
        output_dir / "train_validation_test_split_summary.csv", index=False
    )

    if args.skip_training:
        print("\n--skip-training set: dataset built, skipping CatBoost training.")
        _print_skip_summary(dataset, feature_columns, args.target, output_dir)
        return 0

    outputs = _train_all(
        dataset,
        present_finalists,
        cost_scenarios,
        target=args.target,
        models_dir=output_dir / "models",
    )

    for name, frame in outputs.items():
        path = output_dir / f"{name}.csv"
        frame.to_csv(path, index=False)
        print(f"Wrote {name}.csv ({len(frame)} rows)")

    _print_report(dataset, feature_columns, outputs, output_dir, args.target)
    return 0


def _print_skip_summary(
    dataset: pd.DataFrame, feature_columns: list[str], target: str, output_dir: Path
) -> None:
    print("\n" + "=" * 70)
    print("DATASET-ONLY SUMMARY (--skip-training)")
    print("=" * 70)
    print(f"Dataset rows         : {len(dataset)}")
    print(f"Feature count        : {len(feature_columns)}")
    if target in dataset.columns and len(dataset):
        print(f"Target positive rate : {dataset[target].mean():.4f} ({target})")
    for split in ("train", "validation", "test"):
        n = int((dataset["split"] == split).sum())
        print(f"  {split:<11}rows    : {n}")
    print(f"Output folder        : {output_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
