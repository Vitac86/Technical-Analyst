"""
Export model metadata manifest for future APK integration.

Does NOT integrate the model into the APK runtime — that is a future step.
Produces a JSON manifest capturing feature names, class names, thresholds,
training universe, and version metadata needed to wire up inference later.

If the model has already been trained, also loads and records top feature
importances from the saved .cbm file.

Usage (from repo root):
    python ml/export_model_info.py
    python ml/export_model_info.py --version catboost_direction_v2
"""
import argparse
import json
from datetime import date
from pathlib import Path

import yaml

from features import FEATURE_COLUMNS
from labels import CLASS_NAMES

_ML_DIR = Path(__file__).parent
_REPO_ROOT = _ML_DIR.parent


def load_config(config_path=None) -> dict:
    if config_path is None:
        config_path = _ML_DIR / "config" / "default.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _rpath(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else (_REPO_ROOT / p).resolve()


def load_feature_importances(model_path: Path):
    """Load feature importances from a trained .cbm file if it exists."""
    if not model_path.exists():
        return None
    try:
        from catboost import CatBoostClassifier
        model = CatBoostClassifier()
        model.load_model(str(model_path))
        importances = dict(
            sorted(
                zip(FEATURE_COLUMNS, model.get_feature_importance()),
                key=lambda x: x[1],
                reverse=True,
            )
        )
        return {k: round(float(v), 2) for k, v in importances.items()}
    except Exception as exc:
        print(f"  [warn] Could not load feature importances: {exc}")
        return None


def build_manifest(config: dict, model_version: str, feature_importances) -> dict:
    lbl = config["label"]
    cb = config["train"]["catboost"]
    manifest = {
        "modelVersion": model_version,
        "modelType": "catboost_multiclass",
        "target": "direction_next_candles",
        "classes": CLASS_NAMES,
        "featureNames": FEATURE_COLUMNS,
        "featureCount": len(FEATURE_COLUMNS),
        "horizonCandles": lbl["horizon_candles"],
        "upThresholdPct": lbl["up_threshold_pct"],
        "downThresholdPct": lbl["down_threshold_pct"],
        "trainingConfig": {
            "engine": config["engine"],
            "market": config["market"],
            "board": config["board"],
            "timeframe": config["timeframe"],
            "tickerUniverse": config["tickers"],
            "dateFrom": config["date_from"],
            "dateTo": config["date_to"],
            "catboostParams": {k: v for k, v in cb.items() if k != "verbose"},
        },
        "featureImportances": feature_importances,
        "inferenceNotes": (
            "Model trained on liquid MOEX shares (TQBR board). "
            "Apply primarily to similar liquid MOEX equity instruments. "
            "Not valid for FX, futures, bonds, or illiquid instruments. "
            "The training universe and inference universe should be similar instruments."
        ),
        "futureIntegration": {
            "target": "Replace runMockModel() in frontend/src/ml/mockModel.ts",
            "modelFile": f"frontend/public/models/{model_version}.cbm",
            "manifestFile": "frontend/public/models/model-manifest.json",
            "notes": "APK integration is a future step — do not include in this release.",
        },
        "createdAt": str(date.today()),
        "status": "experimental",
        "disclaimer": "Not financial advice. Experimental research model only.",
    }
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Export CatBoost model metadata manifest")
    parser.add_argument("--config", default=None)
    parser.add_argument("--version", default="catboost_direction_v1")
    args = parser.parse_args()

    config = load_config(args.config)
    models_dir = _rpath(config["output"]["models_dir"])
    model_path = models_dir / f"{args.version}.cbm"

    print(f"Building manifest for version: {args.version}")
    importances = load_feature_importances(model_path)
    if importances:
        print(f"  Loaded feature importances from {model_path.name}")
    else:
        print(f"  Model file not found ({model_path.name}) — importances will be null")

    manifest = build_manifest(config, args.version, importances)

    models_dir.mkdir(parents=True, exist_ok=True)
    out_path = models_dir / f"{args.version}_manifest.json"
    with open(out_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest saved: {out_path}")

    return manifest


if __name__ == "__main__":
    main()
