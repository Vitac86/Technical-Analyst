# ML Training Pipeline — Technical Analyst

Offline PC-side CatBoost training pipeline for a direction-prediction model
targeting liquid MOEX shares on the TQBR board.

**This directory is for PC-side training only.**
Nothing here is required for the Android APK build.
No Python dependencies are added to the frontend.

---

## Instrument scope

The model is trained on a configurable set of liquid MOEX shares (TQBR board):

```
SBER  GAZP  LKOH  ROSN  NVTK  GMKN  TATN  MOEX  AFLT  VTBR
```

**Important:** The model is trained on, and should primarily be applied to,
liquid MOEX equity instruments on the TQBR board.
It is **not valid** for FX pairs, futures, bonds, or illiquid instruments.

---

## Model overview

| Parameter        | Value                          |
|------------------|-------------------------------|
| Type             | CatBoost MultiClass            |
| Output classes   | DOWN (0) / FLAT (1) / UP (2)  |
| Primary TF       | 5m (configurable to 15m)      |
| Horizon          | 3 candles ahead                |
| Up/Down threshold| ±0.25% forward return          |
| Features         | 30 technical indicators        |
| Training period  | 2022-01-01 → 2024-12-31        |

---

## File layout

```
ml/
  README.md                    ← this file
  requirements.txt             ← PC-side Python dependencies
  config/
    default.yaml               ← tickers, timeframe, label settings, catboost params
  moex_download.py             ← download raw 1m candles from MOEX ISS
  features.py                  ← 30 technical features (no future leakage)
  labels.py                    ← UP/DOWN/FLAT labels (close-based)
  build_dataset.py             ← aggregate → features → labels → save parquet
  train_catboost.py            ← time-split, train, evaluate, save .cbm
  validate_walk_forward.py     ← expanding-window walk-forward validation
  export_model_info.py         ← export manifest JSON for future APK integration

  data/                        ← gitignored — created by download/build scripts
    raw/                       ← raw 1m CSVs per ticker
    processed/                 ← aggregated, featurised, labelled parquet
  models/                      ← gitignored (*.cbm) — trained model binaries
  reports/                     ← gitignored — metrics and validation reports
```

---

## Setup

```bash
# From repo root (C:\Projects\Technical-Analyst)
cd C:\Projects\Technical-Analyst

# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# Install dependencies
pip install -r ml/requirements.txt
```

> The virtual environment `.venv/` is gitignored.
> Do not add any of these dependencies to `frontend/package.json`.

---

## Step 1 — Download candles

Downloads 1-minute candles for all configured tickers from MOEX ISS public API.
Raw CSVs are saved to `ml/data/raw/`.

```bash
python ml/moex_download.py
```

Options:

```bash
# Re-download existing files
python ml/moex_download.py --force

# Download only specific tickers
python ml/moex_download.py --tickers SBER GAZP LKOH
```

**Expected time:** ~10–30 minutes for all 10 tickers over 3 years of 1m data.
The script is polite to MOEX servers (0.35s delay between paginated requests).

**Output:** `ml/data/raw/<TICKER>_1m.csv`

---

## Step 2 — Build dataset

Aggregates 1m raw candles to the target timeframe (default 5m), computes
all 30 features, assigns labels, and saves a single Parquet file with
metadata columns (ticker, timeframe, engine, market, board).

```bash
python ml/build_dataset.py
```

Options:

```bash
# Build for 15m instead of the config default
python ml/build_dataset.py --timeframe 15m

# Save as CSV instead of Parquet
python ml/build_dataset.py --csv
```

**Output:** `ml/data/processed/dataset_5m.parquet`

---

## Step 3 — Train model

Loads the processed dataset, applies a time-based split (no random shuffle),
trains CatBoost MultiClass, and evaluates on the held-out validation and test sets.

```bash
python ml/train_catboost.py
```

Options:

```bash
python ml/train_catboost.py --timeframe 15m
```

**Outputs:**

| File | Description |
|------|-------------|
| `ml/models/catboost_direction_v1.cbm` | Trained model binary (gitignored) |
| `ml/models/catboost_direction_v1_manifest.json` | Model metadata |
| `ml/reports/catboost_direction_v1_metrics.json` | Val/test accuracy and classification report |

**Train/val/test split (time-based):**

```
oldest 70% → train
next 15%   → validation (used for early stopping)
latest 15% → test (held-out, reported separately)
```

---

## Step 4 — Walk-forward validation

Evaluates the model across time using an expanding training window.
Each fold trains on all data before the validation window and reports
accuracy, per-class metrics, and signal counts.

```bash
python ml/validate_walk_forward.py
```

Options:

```bash
python ml/validate_walk_forward.py --folds 6 --threshold 0.60
```

**Output:** `ml/reports/catboost_walk_forward_v1.json`

---

## Step 5 — Export model manifest

Exports a JSON manifest with feature names, class names, thresholds,
and training metadata. Used for future APK integration planning.

```bash
python ml/export_model_info.py
```

**Output:** `ml/models/catboost_direction_v1_manifest.json`

---

## Complete run sequence

```bash
cd C:\Projects\Technical-Analyst
python -m venv .venv && .venv\Scripts\activate
pip install -r ml/requirements.txt
python ml/moex_download.py
python ml/build_dataset.py
python ml/train_catboost.py
python ml/validate_walk_forward.py
python ml/export_model_info.py
```

---

## What is gitignored

| Path | Reason |
|------|--------|
| `ml/data/` | Large raw and processed data files |
| `ml/models/*.cbm` | Trained model binaries (up to 50+ MB) |
| `ml/models/*.bin` | Any other binary model artifacts |
| `ml/reports/` | Generated metrics and validation reports |
| `.venv/` | Virtual environment |
| `__pycache__/` | Python bytecode |

**Committed (tracked by git):**
- All `.py` scripts
- `ml/config/default.yaml`
- `ml/README.md`
- `ml/requirements.txt`
- `ml/models/*_manifest.json` (metadata only, no binary)

---

## Configuration

Edit `ml/config/default.yaml` to change tickers, timeframe, label thresholds,
date range, or CatBoost hyperparameters.

```yaml
tickers: [SBER, GAZP, ...]     # training universe
timeframe: 5m                  # 5m or 15m
date_from: "2022-01-01"
date_to:   "2024-12-31"
label:
  horizon_candles: 3
  up_threshold_pct: 0.25
  down_threshold_pct: 0.25
```

---

## Future APK integration (not this step)

When the model is validated and ready for inference:

1. Copy `catboost_direction_v1.cbm` → `frontend/public/models/`
2. Update `frontend/public/models/model-manifest.json`
3. Replace `runMockModel()` in `frontend/src/ml/mockModel.ts` with real CatBoost inference
4. Bump `modelVersion` in the manifest
5. Rebuild APK

Feature order in training (`FEATURE_COLUMNS` in `features.py`) must match the
frontend inference code. The current frontend scaffold uses 17 features
(`AI_FEATURE_NAMES` in `frontend/src/ml/types.ts`); the training pipeline uses
30 features including additional training-only indicators. Alignment between
training and inference will need to be resolved at APK integration time.

---

## Disclaimer

This model is experimental and for research purposes only.
**It is not financial advice.**
Past performance on historical data does not guarantee future results.
The model has not been audited for production use.
