"""
Export the binary SHORT CatBoost model to JSON for APK integration.

Usage (from repo root):
    python ml\\export_app_model.py

Output:
    frontend/public/models/pa_short_v0/model.json
    frontend/public/models/pa_short_v0/manifest.json

Safety:
    - Stops if model.json > 3 MB (do not blindly commit).
    - Does NOT copy or export the .cbm binary.
    - Does NOT commit anything — run git add manually after review.
"""
import json
import sys
from pathlib import Path

_ML_DIR = Path(__file__).parent
_REPO   = _ML_DIR.parent

_MODEL_CBM  = _ML_DIR / "models" / "catboost_pa_short_focused_tp_sl_h12.cbm"
_OUT_DIR    = _REPO / "frontend" / "public" / "models" / "pa_short_v0"
_MODEL_JSON = _OUT_DIR / "model.json"
_MANIFEST   = _OUT_DIR / "manifest.json"
_MAX_MB     = 3.0

sys.path.insert(0, str(_ML_DIR))
from features import FEATURE_COLUMNS
from price_action import PRICE_ACTION_FEATURE_COLUMNS

ALL_FEATURE_COLS: list[str] = FEATURE_COLUMNS + [
    c for c in PRICE_ACTION_FEATURE_COLUMNS if c not in FEATURE_COLUMNS
]


def main() -> None:
    if not _MODEL_CBM.exists():
        print(f"ERROR: Model not found: {_MODEL_CBM}")
        print("Run  python ml\\experiments_price_action.py  to train the model first.")
        sys.exit(1)

    try:
        from catboost import CatBoostClassifier
    except ImportError:
        print("ERROR: catboost not installed. Run: pip install catboost")
        sys.exit(1)

    print(f"Loading: {_MODEL_CBM.name}")
    model = CatBoostClassifier()
    model.load_model(str(_MODEL_CBM))

    # Validate / report feature names
    model_features: list[str] = list(model.feature_names_)
    if model_features != ALL_FEATURE_COLS:
        print("WARNING: model feature names differ from expected ALL_FEATURE_COLS")
        print(f"  Expected {len(ALL_FEATURE_COLS)}: {ALL_FEATURE_COLS[:4]}...")
        print(f"  Model    {len(model_features)}: {model_features[:4]}...")
        print("  Using model's own feature names in manifest.")
        manifest_features = model_features
    else:
        manifest_features = ALL_FEATURE_COLS

    print(f"Features: {len(manifest_features)}")
    print(f"Classes : {model.classes_}")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Export model to JSON
    print(f"Exporting: {_MODEL_JSON}")
    model.save_model(str(_MODEL_JSON), format="json")

    size_mb = _MODEL_JSON.stat().st_size / (1024 * 1024)
    print(f"model.json: {size_mb:.2f} MB")

    if size_mb > _MAX_MB:
        print(f"\nERROR: {size_mb:.2f} MB exceeds the {_MAX_MB} MB limit.")
        print("Do NOT commit this file. Investigate model compression.")
        _MODEL_JSON.unlink()
        sys.exit(1)

    print(f"OK: within {_MAX_MB} MB limit")

    # Build manifest
    manifest = {
        "modelId": "pa_short_v0",
        "sourceModel": "catboost_pa_short_focused_tp_sl_h12",
        "modelType": "binary_short",
        "version": "0.1.0-research",
        "featureSet": "technical_plus_price_action_v1",
        "featureCount": len(manifest_features),
        "featureNames": manifest_features,
        "requiredMinCandles": 120,
        "thresholds": {
            "watch":    0.55,
            "elevated": 0.65,
            "high":     0.70,
        },
        "labelConfig": {
            "mode":           "tp_sl",
            "horizonCandles": 12,
            "takeProfitPct":  0.4,
            "stopLossPct":    0.25,
        },
        "backtestStatus": {
            "validated":               False,
            "passedPromisingCriteria": False,
            "bestProfitFactor":        0.3421,
            "note": (
                "Research model. Backtest did not show positive net expectancy."
            ),
        },
        "classNames": ["NOT_SHORT", "SHORT"],
    }

    with open(_MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"manifest.json written ({_MANIFEST.stat().st_size} bytes)")
    print(f"\nFeature list ({len(manifest_features)}):")
    for i, name in enumerate(manifest_features):
        print(f"  {i:2d}  {name}")
    print("\nExport complete. Review model.json before committing.")


if __name__ == "__main__":
    main()
