"""Strategy Lab v1.5.1 runner: ML signal-filter ablation experiments.

This extends the v1.5 pipeline (in :mod:`ml_signal_filter`) with controlled
*ablation* experiments. The v1.5 CatBoost filter trained on every feature and
failed to transfer to the 2025-2026 test period; v1.5.1 trains a **separate
model per feature-set mode** and adds a percentile-based selection method so we
can isolate the cause:

    1. build (or reuse) the leakage-safe dataset of executed rule-based trade
       candidates (one model-agnostic dataset carrying every feature column),
    2. time-split it into train / validation / test by ``signal_time``,
    3. for every (finalist x cost_scenario x feature_set) combination train a
       CatBoost classifier on the train period,
    4. choose a selection rule -- a probability threshold *or* a top-percentile
       cutoff -- on the **validation** period only,
    5. apply it once to the held-out test period and compare filtered vs
       unfiltered trades, and roll everything up into ``ablation_summary.csv``.

It writes research CSVs (and optionally models) under a git-ignored reports
folder and prints a console summary. Research only -- it never trades, adds no
UI, and never modifies the MT5 exports. CatBoost stays lazily imported, so the
``--skip-training`` dataset-only path needs no ML dependencies.

Run directly::

    python backend/app/strategy_lab/run_ml_signal_filter.py --finalist all \
        --cost-scenario all --feature-set all

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
OUTPUT_DIR = REPO_ROOT / "MetaTrader_Data" / "reports" / "ml_signal_filter_v1_5_1"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strategy Lab v1.5.1 ML signal-filter ablation (CatBoost)"
    )
    parser.add_argument("--finalist", choices=["D", "C", "all"], default="all")
    parser.add_argument(
        "--cost-scenario",
        choices=["Base", "Conservative", "Stress", "all"],
        default="all",
    )
    parser.add_argument(
        "--feature-set",
        choices=[*ml_features.FEATURE_SET_MODES, "all"],
        default="all_features",
        help="Feature-set ablation mode (or 'all' to run every mode).",
    )
    parser.add_argument(
        "--selection-method",
        choices=[*ml_signal_filter.SELECTION_METHODS, "both"],
        default="both",
        help="Validation selection method(s): probability_threshold, "
        "top_percent, or both (default).",
    )
    parser.add_argument(
        "--threshold-min",
        type=float,
        default=ml_signal_filter.DEFAULT_THRESHOLD_MIN,
        help="Lowest probability threshold scanned on validation (default 0.20).",
    )
    parser.add_argument(
        "--threshold-max",
        type=float,
        default=ml_signal_filter.DEFAULT_THRESHOLD_MAX,
        help="Highest probability threshold scanned on validation (default 0.90).",
    )
    parser.add_argument(
        "--threshold-step",
        type=float,
        default=ml_signal_filter.DEFAULT_THRESHOLD_STEP,
        help="Probability-threshold grid step (default 0.02).",
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
        "--save-models",
        action="store_true",
        help="Persist each fitted CatBoost model under <output-dir>/models/ "
        "(off by default -- a full ablation trains dozens of models).",
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


def _resolve_feature_sets(arg: str) -> list[str]:
    return list(ml_features.FEATURE_SET_MODES) if arg == "all" else [arg]


def _resolve_selection_methods(arg: str) -> list[str]:
    return list(ml_signal_filter.SELECTION_METHODS) if arg == "both" else [arg]


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
    """Per (finalist, feature) row with its source TF and feature-set membership.

    The boolean ``in_<mode>`` columns document exactly which ablation modes use
    each feature -- the canonical reference for interpreting the experiments.
    """
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for finalist in finalists:
        cols = ml_features.feature_columns_for_finalist(finalist)
        membership = {
            mode: set(ml_features.select_feature_set(cols, mode))
            for mode in ml_features.FEATURE_SET_MODES
        }
        for col in cols:
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
            row = {"finalist": finalist, "feature": col, "source": source}
            for mode in ml_features.FEATURE_SET_MODES:
                row[f"in_{mode}"] = col in membership[mode]
            rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Training orchestration (ablation: finalist x cost_scenario x feature_set)
# ---------------------------------------------------------------------------

def _combo_outputs(
    frame: pd.DataFrame,
    *,
    finalist: str,
    cost: str,
    feature_set: str,
    feat_cols: list[str],
    target: str,
    methods: list[str],
    thresholds,
) -> dict | None:
    """Train + evaluate a single (finalist, cost, feature_set) combination.

    Returns the per-table fragments for this combo, or ``None`` when the model
    could not be trained (empty / single-class split). The selection cutoff is
    chosen on validation only and applied once to test.
    """
    trained = ml_signal_filter.train_model(
        frame, feat_cols, finalist=finalist, cost_scenario=cost, target=target
    )
    if trained is None:
        return None

    val = frame[frame["split"] == "validation"]
    test = frame[frame["split"] == "test"]
    val_prob = ml_signal_filter.predict_proba(trained, val)
    test_prob = ml_signal_filter.predict_proba(trained, test)

    choice = ml_signal_filter.search_selection(
        val,
        val_prob,
        finalist=finalist,
        cost_scenario=cost,
        feature_set=feature_set,
        target=target,
        methods=methods,
        thresholds=thresholds,
    )
    filtered_row = ml_signal_filter.filtered_backtest_row(
        test,
        test_prob,
        finalist=finalist,
        cost_scenario=cost,
        feature_set=feature_set,
        target=target,
        choice=choice,
    )
    test_auc = ml_signal_filter.evaluate_auc(trained, test)
    ablation = ml_signal_filter.ablation_row(
        filtered_row,
        feature_count=len(feat_cols),
        validation_auc=trained.validation_auc,
        test_auc=test_auc,
    )

    diagnostics: list[dict] = []
    deciles: list[dict] = []
    for split_name, sframe, sprob in (
        ("validation", val, val_prob),
        ("test", test, test_prob),
    ):
        diagnostics.append(
            ml_signal_filter.probability_diagnostics_row(
                sprob,
                finalist=finalist,
                cost_scenario=cost,
                feature_set=feature_set,
                target=target,
                split=split_name,
            )
        )
        deciles.extend(
            ml_signal_filter.probability_decile_rows(
                sframe,
                sprob,
                finalist=finalist,
                cost_scenario=cost,
                feature_set=feature_set,
                target=target,
                split=split_name,
            )
        )

    model_row = {
        "finalist": finalist,
        "cost_scenario": cost,
        "feature_set": feature_set,
        "target": target,
        "feature_count": len(feat_cols),
        "train_rows": trained.train_rows,
        "validation_rows": trained.validation_rows,
        "test_rows": trained.test_rows,
        "train_auc": trained.train_auc,
        "validation_auc": trained.validation_auc,
        "test_auc": test_auc,
        "best_iteration": trained.best_iteration,
        "selected_method": choice.selection_method,
        "selected_cutoff": choice.cutoff_value,
        "probability_cutoff": choice.probability_cutoff,
        "validation_score": choice.validation_score,
        "threshold_status": choice.status,
    }
    return {
        "trained": trained,
        "choice": choice,
        "threshold_table": choice.table,
        "filtered_row": filtered_row,
        "ablation_row": ablation,
        "model_row": model_row,
        "diagnostics": diagnostics,
        "deciles": deciles,
        "walk_forward": ml_signal_filter.walk_forward_rows(
            frame,
            trained,
            feature_set=feature_set,
            probability_cutoff=choice.probability_cutoff,
            threshold_status=choice.status,
        ),
        "importance": ml_signal_filter.feature_importance_rows(
            trained, feature_set=feature_set
        ),
    }


def _train_all(
    dataset: pd.DataFrame,
    finalists: list[str],
    cost_scenarios: list[str],
    feature_sets: list[str],
    *,
    target: str,
    methods: list[str],
    thresholds,
    models_dir: Path,
    save_models: bool,
) -> dict[str, pd.DataFrame]:
    """Run the full ablation grid and collect every output table."""
    threshold_tables: list[pd.DataFrame] = []
    model_rows: list[dict] = []
    filtered_rows: list[dict] = []
    ablation_rows: list[dict] = []
    walk_forward_rows: list[dict] = []
    importance_rows: list[dict] = []
    diagnostics_rows: list[dict] = []
    decile_rows: list[dict] = []

    if save_models:
        import joblib  # local import: only needed when persisting models

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        models_dir.mkdir(parents=True, exist_ok=True)

    for finalist in finalists:
        base_cols = [
            c
            for c in ml_features.feature_columns_for_finalist(finalist)
            if c in dataset.columns
        ]
        for cost in cost_scenarios:
            frame = dataset[
                (dataset["finalist"] == finalist)
                & (dataset["cost_scenario"] == cost)
            ].copy()
            if frame.empty:
                continue
            for feature_set in feature_sets:
                feat_cols = ml_features.select_feature_set(base_cols, feature_set)
                if not feat_cols:
                    print(f"  [skip] {finalist}/{cost}/{feature_set}: no feature columns")
                    continue
                print(
                    f"\n=== {finalist} / {cost} / {feature_set} "
                    f"({len(feat_cols)} features) ==="
                )
                combo = _combo_outputs(
                    frame,
                    finalist=finalist,
                    cost=cost,
                    feature_set=feature_set,
                    feat_cols=feat_cols,
                    target=target,
                    methods=methods,
                    thresholds=thresholds,
                )
                if combo is None:
                    continue

                threshold_tables.append(combo["threshold_table"])
                model_rows.append(combo["model_row"])
                filtered_rows.append(combo["filtered_row"])
                ablation_rows.append(combo["ablation_row"])
                walk_forward_rows.extend(combo["walk_forward"])
                importance_rows.extend(combo["importance"])
                diagnostics_rows.extend(combo["diagnostics"])
                decile_rows.extend(combo["deciles"])

                choice = combo["choice"]
                print(
                    f"  val_auc={combo['model_row']['validation_auc']:.3f} "
                    f"method={choice.selection_method} cutoff={choice.cutoff_value:g} "
                    f"prob>={choice.probability_cutoff:.3f} status={choice.status}"
                )

                if save_models:
                    model_path = (
                        models_dir
                        / f"catboost_{finalist}_{cost}_{feature_set}_{timestamp}.joblib"
                    )
                    joblib.dump(
                        {
                            "model": combo["trained"].model,
                            "feature_columns": combo["trained"].feature_columns,
                            "finalist": finalist,
                            "cost_scenario": cost,
                            "feature_set": feature_set,
                            "target": target,
                            "selection_method": choice.selection_method,
                            "cutoff_value": choice.cutoff_value,
                            "probability_cutoff": choice.probability_cutoff,
                            "threshold_status": choice.status,
                        },
                        model_path,
                    )
                    print(f"  saved model -> {model_path.name}")

    ablation_summary = pd.DataFrame(ablation_rows)
    return {
        "model_training_summary": pd.DataFrame(model_rows),
        "threshold_search": (
            pd.concat(threshold_tables, ignore_index=True)
            if threshold_tables
            else pd.DataFrame()
        ),
        "probability_diagnostics": pd.DataFrame(diagnostics_rows),
        "probability_deciles": pd.DataFrame(decile_rows),
        "filtered_backtest_summary": pd.DataFrame(filtered_rows),
        "walk_forward_ml_summary": pd.DataFrame(walk_forward_rows),
        "feature_importance": pd.DataFrame(importance_rows),
        "ablation_summary": ablation_summary,
        "top_filtered_candidates": ml_signal_filter.rank_candidates(ablation_summary),
    }


# ---------------------------------------------------------------------------
# Console report (spec section 9)
# ---------------------------------------------------------------------------

def _print_ablation_table(df: pd.DataFrame, head: int | None = None) -> None:
    cols = [
        "finalist",
        "cost_scenario",
        "feature_set",
        "feature_count",
        "validation_auc",
        "test_auc",
        "selected_method",
        "selected_cutoff",
        "threshold_status",
        "filtered_test_trades",
        "improvement_pf",
        "improvement_average_r",
        "improvement_drawdown",
        "ml_filter_score",
    ]
    cols = [c for c in cols if c in df.columns]
    view = df if head is None else df.head(head)
    with pd.option_context("display.max_columns", None, "display.width", 260):
        print(view[cols].to_string(index=False))


def _print_probability_summary(diagnostics: pd.DataFrame) -> None:
    """Compressed-probability diagnostic summary (q10/q50/q90 + spread)."""
    if diagnostics.empty:
        print("  (no probability diagnostics)")
        return
    for split in ("validation", "test"):
        grp = diagnostics[diagnostics["split"] == split]
        if grp.empty:
            continue
        spread = (grp["q90"] - grp["q10"]).mean()
        print(
            f"  {split:<11} mean q10={grp['q10'].mean():.3f} "
            f"q50={grp['q50'].mean():.3f} q90={grp['q90'].mean():.3f} "
            f"| mean(q90-q10)={spread:.3f}  (small spread => compressed probabilities)"
        )


def _print_report(
    dataset: pd.DataFrame,
    outputs: dict[str, pd.DataFrame],
    output_dir: Path,
    target: str,
) -> None:
    print("\n" + "=" * 78)
    print("STRATEGY LAB v1.5.1 -- ML SIGNAL FILTER ABLATION SUMMARY")
    print("=" * 78)

    ablation = outputs.get("ablation_summary", pd.DataFrame())
    print(f"Total dataset rows            : {len(dataset)}")
    print(f"Combinations tested           : {len(ablation)} "
          f"(finalist x cost_scenario x feature_set)")
    if target in dataset.columns and len(dataset):
        print(f"Target ({target}) positive rate: {dataset[target].mean():.4f}")

    if not ablation.empty:
        top = ablation.sort_values("ml_filter_score", ascending=False)
        print("\nTop 20 ablation rows by ml_filter_score:")
        _print_ablation_table(top, head=20)

    improved = outputs.get("top_filtered_candidates", pd.DataFrame())
    print("\nRows that genuinely improved the test metrics (passed ranking gates):")
    if improved is None or improved.empty:
        print("  (none -- the ML filter did not beat the rule-based finalist on test)")
    else:
        _print_ablation_table(improved)

    print("\nProbability diagnostic summary:")
    _print_probability_summary(outputs.get("probability_diagnostics", pd.DataFrame()))

    print(f"\nOutput folder                 : {output_dir}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    finalists = _resolve_finalists(args.finalist)
    cost_scenarios = _resolve_cost_scenarios(args.cost_scenario)
    feature_sets = _resolve_feature_sets(args.feature_set)
    methods = _resolve_selection_methods(args.selection_method)
    thresholds = ml_signal_filter.build_threshold_grid(
        args.threshold_min, args.threshold_max, args.threshold_step
    )
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
        _print_skip_summary(dataset, feature_columns, feature_sets, args.target, output_dir)
        return 0

    print(
        f"\nAblation grid: {len(present_finalists)} finalist(s) x "
        f"{len(cost_scenarios)} cost scenario(s) x {len(feature_sets)} feature set(s); "
        f"selection methods={methods}; {len(thresholds)} thresholds."
    )
    outputs = _train_all(
        dataset,
        present_finalists,
        cost_scenarios,
        feature_sets,
        target=args.target,
        methods=methods,
        thresholds=thresholds,
        models_dir=output_dir / "models",
        save_models=args.save_models,
    )

    for name, frame in outputs.items():
        path = output_dir / f"{name}.csv"
        frame.to_csv(path, index=False)
        print(f"Wrote {name}.csv ({len(frame)} rows)")

    _print_report(dataset, outputs, output_dir, args.target)
    return 0


def _print_skip_summary(
    dataset: pd.DataFrame,
    feature_columns: list[str],
    feature_sets: list[str],
    target: str,
    output_dir: Path,
) -> None:
    print("\n" + "=" * 78)
    print("DATASET-ONLY SUMMARY (--skip-training)")
    print("=" * 78)
    print(f"Dataset rows         : {len(dataset)}")
    print(f"Feature count (all)  : {len(feature_columns)}")
    print(f"Feature-set modes    : {feature_sets}")
    if target in dataset.columns and len(dataset):
        print(f"Target positive rate : {dataset[target].mean():.4f} ({target})")
    for split in ("train", "validation", "test"):
        n = int((dataset["split"] == split).sum())
        print(f"  {split:<11}rows    : {n}")
    print(f"Output folder        : {output_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
