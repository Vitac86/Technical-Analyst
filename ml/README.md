# ML Training Pipeline — Technical Analyst

Offline PC-side CatBoost training pipeline for a direction-prediction model
targeting liquid MOEX shares on the TQBR board.

**This directory is for PC-side training only.**
Nothing here is required for the Android APK build.
No Python dependencies are added to the frontend.

---

## Why accuracy is misleading here

The raw dataset has a severe class imbalance:

| Class | Approx share |
|-------|-------------|
| FLAT  | ~79%        |
| UP    | ~10%        |
| DOWN  | ~10%        |

A model that predicts FLAT for every row achieves ~79% accuracy.
That is useless for trading — it generates zero signals.

**What matters instead:**
- UP precision and recall
- DOWN precision and recall
- Signal-level precision at various probability thresholds
- Coverage (what fraction of candles get a non-FLAT signal)
- Average future return for rows where a signal fired

---

## Class imbalance handling

Two training variants are produced:

| Variant | Mode | What it does |
|---------|------|--------------|
| `catboost_direction_v1_baseline` | none | No weighting — reproduces original behaviour |
| `catboost_direction_v2_balanced` | balanced | `auto_class_weights="Balanced"` in CatBoost |

Config also supports `manual` mode with explicit per-class weights:

```yaml
training:
  class_balance:
    mode: "manual"
    manual_weights:
      DOWN: 3.0
      FLAT: 1.0
      UP:   3.0
```

The balanced variant significantly improves UP/DOWN recall at a small cost to FLAT recall.

---

## Signal-level evaluation

Hard class predictions at 0.5 are not enough.
`evaluate_signals.py` scans a range of probability thresholds and reports:

**Signal rules:**
- `LONG`     if P(UP) ≥ threshold AND P(UP) > P(DOWN)
- `SHORT`    if P(DOWN) ≥ threshold AND P(DOWN) > P(UP)
- `NO_TRADE` otherwise

**Per threshold reports:**
- long_signals count
- short_signals count
- no_trade count
- LONG precision (fraction of LONG signals where actual label == UP)
- SHORT precision
- combined signal accuracy
- coverage % (signals / total rows)
- average future return for LONG/SHORT (if `store_future_returns: true` in config)
- per-ticker breakdown

Higher thresholds → fewer signals, higher precision, lower coverage.

---

## TP/SL labeling

The default `close` mode labels based on close return after N candles.
The `tp_sl` mode examines the actual price path:

- **UP (2):**  price reaches +TP% before touching -SL% within horizon candles
- **DOWN (0):** price reaches -SL% (i.e. downside TP) before +SL%
- **FLAT (1):** neither side triggered, or both hit on same candle

Config:

```yaml
label:
  mode: tp_sl
  horizon_candles: 6
  take_profit_pct: 0.30
  stop_loss_pct: 0.20
  flat_if_both_hit_same_candle: true
```

TP/SL labels look into the future — that is intentional; they are targets, not features.
Label creation is applied per-ticker to avoid cross-ticker leakage at boundaries.

---

## Instrument scope

```
SBER  GAZP  LKOH  ROSN  NVTK  GMKN  TATN  MOEX  AFLT  VTBR
```

Liquid MOEX equity on the TQBR board only.
Not valid for FX, futures, bonds, or illiquid instruments.

---

## Model overview

| Parameter         | Value                          |
|-------------------|-------------------------------|
| Type              | CatBoost MultiClass            |
| Output classes    | DOWN (0) / FLAT (1) / UP (2)  |
| Primary TF        | 5m (configurable)             |
| Default horizon   | 3 candles ahead (close mode)  |
| Up/Down threshold | ±0.25% forward return          |
| Features          | 30 technical indicators        |
| Training period   | 2022-01-01 → 2024-12-31        |

---

## File layout

```
ml/
  README.md                    ← this file
  requirements.txt
  config/
    default.yaml               ← all settings
  moex_download.py             ← download raw 1m candles
  features.py                  ← 30 features (no future leakage)
  labels.py                    ← UP/DOWN/FLAT labels (close + tp_sl modes)
  build_dataset.py             ← aggregate → features → labels → parquet
  train_catboost.py            ← trains baseline + balanced variants
  validate_walk_forward.py     ← expanding-window walk-forward validation
  evaluate_signals.py          ← signal threshold analysis at P(UP/DOWN) thresholds
  experiments.py               ← runs 5 labeling/config experiments and summarises
  export_model_info.py         ← export manifest JSON for future APK integration
  fractal_features.py          ← confirmed fractal detection (no future leakage)
  price_action.py              ← structure trend, BoS, ChoCh, sweeps, range features
  backtest_fractals.py         ← rule-based fractal strategy backtest
  experiments_price_action.py  ← CatBoost experiments with PA features
  backtest_ml_signals.py       ← offline backtest for ML SHORT/LONG signals

  data/                        ← gitignored
    raw/                       ← raw 1m CSVs per ticker
    processed/                 ← labelled parquet
  models/                      ← *.cbm gitignored; *_manifest.json tracked
  reports/                     ← gitignored; all JSON outputs
    experiments/               ← per-experiment + summary JSON
```

---

## Setup

```
cd C:\Projects\Technical-Analyst
python -m venv .venv
.venv\Scripts\activate
pip install -r ml/requirements.txt
```

---

## BCS instrument discovery

Offline BCS instrument discovery lives under `ml/instruments/` and is not used
by the Android build.

Auth uses a BCS refresh token from the environment only:

```
$env:BCS_REFRESH_TOKEN = "your_read_only_refresh_token"
$env:BCS_CLIENT_ID = "trade-api-read"   # optional, this is the default
```

Fetch instruments by official BCS information type:

```
python ml\instruments\bcs_instruments_by_type.py --type GOODS
python ml\instruments\bcs_instruments_by_type.py --type FUTURES
python ml\instruments\bcs_instruments_by_type.py --all-types
```

Endpoint path:

```
https://be.broker.ru/trade-api-information-service/api/v1/instruments/by-type
```

The script exposes `BCS_INSTRUMENTS_BY_TYPE_PATH` for quick adjustment if BCS
renames the route. The current official docs use `size` as the page-size query
parameter; the CLI still calls this option `--limit`.

Outputs:

```
ml/data/instruments/bcs_GOODS.json
ml/reports/instruments/bcs_GOODS_summary.json
ml/reports/instruments/bcs_instruments_by_type_summary.json
```

Commodity discovery uses `type=GOODS` first and falls back to candidate tickers
through the BCS `by-tickers` information endpoint only if the GOODS endpoint
fails:

```
python ml\instruments\discover_bcs_commodities.py
```

Output:

```
ml/reports/instruments/bcs_commodities.json
```

Reports include normalized samples, class/board distributions, instrument type
distribution, currency distribution, ambiguous instruments, and suggested MOEX
match keys (`ticker`, `classCode`, `isin`). Full raw BCS items are omitted by
default; pass `--include-raw` only for local diagnostics.

---

## Step 1 — Download candles

```
python ml\moex_download.py
```

Options:

```
python ml\moex_download.py --force
python ml\moex_download.py --tickers SBER GAZP LKOH
```

Expected time: ~10–30 minutes for all 10 tickers.

---

## Step 2 — Build dataset

```
python ml\build_dataset.py
```

Output: `ml/data/processed/dataset_5m.parquet`

---

## Step 3 — Train model

```
python ml\train_catboost.py
```

Trains both variants by default. To train only one:

```
python ml\train_catboost.py --variant baseline
python ml\train_catboost.py --variant balanced
```

Outputs per variant:

| File | Description |
|------|-------------|
| `ml/models/catboost_direction_v2_balanced.cbm` | Model binary (gitignored) |
| `ml/models/catboost_direction_v2_balanced_manifest.json` | Metadata (tracked) |
| `ml/reports/catboost_direction_v2_balanced_metrics.json` | Accuracy, confusion matrix, per-ticker |

---

## Step 4 — Walk-forward validation

```
python ml\validate_walk_forward.py
```

Options:

```
python ml\validate_walk_forward.py --folds 6 --threshold 0.60
python ml\validate_walk_forward.py --model catboost_direction_v2_balanced
```

Output: `ml/reports/catboost_walk_forward_<model>.json`

---

## Step 5 — Signal threshold analysis

```
python ml\evaluate_signals.py
python ml\evaluate_signals.py --model catboost_direction_v1_baseline
python ml\evaluate_signals.py --split both
```

Output: `ml/reports/<model>_signals.json`

---

## Step 6 — Run experiments

```
python ml\experiments.py
```

Run a subset:

```
python ml\experiments.py --experiments 1 2 3
python ml\experiments.py --dry-run
```

Experiments defined:

| ID | Name | Label mode | Horizon |
|----|------|-----------|---------|
| 1 | close_based_h3_thr025_balanced | close | 3 |
| 2 | close_based_h6_thr025_balanced | close | 6 |
| 3 | close_based_h6_thr020_balanced | close | 6 |
| 4 | tp_sl_h6_tp030_sl020_balanced  | tp_sl | 6 |
| 5 | tp_sl_h12_tp040_sl025_balanced | tp_sl | 12 |

**Note:** TP/SL experiments (4, 5) are significantly slower — allow 5–15 minutes per experiment.

Outputs:

```
ml/reports/experiments/close_based_h3_thr025_balanced.json
ml/reports/experiments/summary.json
```

---

## Step 7 — Export manifest

```
python ml\export_model_info.py
python ml\export_model_info.py --version catboost_direction_v2_balanced
```

---

## Complete run sequence

```
cd C:\Projects\Technical-Analyst
python -m venv .venv && .venv\Scripts\activate
pip install -r ml/requirements.txt
python ml\moex_download.py
python ml\build_dataset.py
python ml\train_catboost.py
python ml\validate_walk_forward.py
python ml\evaluate_signals.py
python ml\experiments.py
python ml\export_model_info.py
```

---

## What to inspect in reports

After training, open these files:

1. `ml/reports/catboost_direction_v2_balanced_metrics.json`
   — Check `test.per_class` for UP/DOWN precision & recall.
   — Check `test.confusion_matrix` for where the model confuses classes.
   — Check `test.per_ticker` to see which tickers perform best.

2. `ml/reports/catboost_direction_v2_balanced_signals.json`
   — Find the threshold row where `long_precision` or `short_precision` is acceptable.
   — Check `coverage_pct` — too low means too few signals for practical use.

3. `ml/reports/experiments/summary.json`
   — Compare UP/DOWN precision & recall across label configurations.

4. `ml/models/catboost_direction_v2_balanced_manifest.json`
   — Feature importances show which indicators matter most.

---

## What is gitignored

| Path | Reason |
|------|--------|
| `ml/data/` | Large raw and processed data files |
| `ml/models/*.cbm` | Trained model binaries |
| `ml/reports/` | Generated metrics and reports |
| `.venv/` | Virtual environment |
| `__pycache__/` | Python bytecode |

**Tracked:**
- All `.py` scripts
- `ml/config/default.yaml`
- `ml/README.md`
- `ml/requirements.txt`
- `ml/models/*_manifest.json`

---

## Current model status (v1 baseline result)

The v1 baseline without class balancing predicts FLAT almost exclusively:

- Test accuracy ≈ 0.759 (misleading — reflects FLAT dominance)
- DOWN recall ≈ 0.017
- UP recall ≈ 0.020
- FLAT recall ≈ 0.995

**Root cause:** CatBoost minimises cross-entropy loss. With FLAT at 79% frequency,
the optimal strategy without class weights is to predict FLAT for nearly every row.
The model learns real signal features but the decision boundary is overwhelmed by
the prior class probability.

**Fix applied in v2:** `auto_class_weights="Balanced"` re-weights the loss so
UP and DOWN errors are penalised proportionally more, forcing the model to learn
minority-class boundaries.

---

## Future APK integration (not this step)

Do NOT integrate the model into the APK yet.
The model needs further validation before production use.

When ready:
1. Copy `catboost_direction_v2_balanced.cbm` → `frontend/public/models/`
2. Update `frontend/public/models/model-manifest.json`
3. Replace `runMockModel()` in `frontend/src/ml/mockModel.ts`
4. Align `FEATURE_COLUMNS` (30 features) with frontend `AI_FEATURE_NAMES` (17 features)
5. Bump `modelVersion` in manifest
6. Rebuild APK

---

---

## Price action / fractal research (new)

This research direction tests whether fractal-based structural features
improve signal quality before any APK integration is considered.

### Why confirmed fractals?

A fractal high at bar *i* requires `right_span` future candles to close
before it can be identified. It is therefore only **confirmed** at bar
`i + right_span`. Using the fractal price at bar *i* itself would be
future leakage.

All usable columns (`last_fractal_high`, `bars_since_fractal_high`, …) are
forward-filled from the confirmation bar. The raw `fractal_high_price`
column (placed at bar *i*) is provided for diagnostics only and **must not**
be used as a model feature or backtest signal.

### Leakage check summary

| Column | Safe? | Reason |
|--------|-------|--------|
| `fractal_high_price` | **NO — diagnostic only** | Requires right-side candles |
| `confirmed_fractal_high_price` | Yes | Placed at `i + right_span` |
| `last_fractal_high` | Yes | Forward-filled from confirmation bar |
| `bars_since_fractal_high` | Yes | Counts from confirmation event |
| `distance_to_last_fractal_high_pct` | Yes | Uses confirmed level |
| All `price_action.py` columns | Yes | Derived from confirmed levels only |

### Fractal definition

Fractal high at bar *i* (`left_span=2`, `right_span=2`):
```
high[i] > high[i-1], high[i-2]   (left side)
high[i] > high[i+1], high[i+2]   (right side — requires future bars)
→ confirmed at bar i+2
```

Fractal low: same with `low` and `<` direction.

### New files

```
ml/
  fractal_features.py      ← confirmed fractal detection, no-leakage columns
  price_action.py          ← structure trend, BoS, ChoCh, sweeps, range features
  backtest_fractals.py     ← rule-based strategy backtest (4 strategies)
  experiments_price_action.py  ← CatBoost experiments with PA features (3 experiments)
  backtest_ml_signals.py   ← offline backtest for ML-generated SHORT/LONG signals
```

### Step 7 — Run fractal backtest

```
python ml\backtest_fractals.py
```

Options:
```
python ml\backtest_fractals.py --no-volume-filter
python ml\backtest_fractals.py --tickers SBER GAZP LKOH
python ml\backtest_fractals.py --tp 0.5 --sl 0.3 --horizon 16
```

Strategies backtested:

| Strategy | Direction | Entry condition |
|----------|-----------|-----------------|
| `fractal_breakout_long`   | LONG  | BoS up + bullish/neutral structure + vol > 0 |
| `fractal_breakdown_short` | SHORT | BoS down + bearish/neutral structure + vol > 0 |
| `sweep_reversal_long`     | LONG  | Low sweeps fractal low, close recovers |
| `sweep_reversal_short`    | SHORT | High sweeps fractal high, close falls back |

Outputs:
```
ml/reports/fractals/fractal_backtest_summary.json
ml/reports/fractals/fractal_breakout_long.json
ml/reports/fractals/fractal_breakdown_short.json
ml/reports/fractals/sweep_reversal_long.json
ml/reports/fractals/sweep_reversal_short.json
```

### Step 8 — Run price action ML experiments

```
python ml\experiments_price_action.py
```

Run a subset:
```
python ml\experiments_price_action.py --experiments 1 2
python ml\experiments_price_action.py --dry-run
```

Experiments:

| ID | Name | Label | Horizon | Classifier |
|----|------|-------|---------|------------|
| 1 | `catboost_pa_close_h6_thr020_balanced` | close | 6 | multiclass |
| 2 | `catboost_pa_tp_sl_h12_tp040_sl025_balanced` | tp_sl | 12 | multiclass |
| 3 | `catboost_pa_short_focused_tp_sl_h12` | tp_sl | 12 | binary SHORT |

Experiment 3 trains a binary classifier:
- Class 1 = SHORT_SETUP (original DOWN label)
- Class 0 = NOT_SHORT
- Motivated by prior finding that SHORT signals are more promising than LONG.

Outputs:
```
ml/reports/price_action/catboost_pa_close_h6_thr020_balanced.json
ml/reports/price_action/catboost_pa_tp_sl_h12_tp040_sl025_balanced.json
ml/reports/price_action/catboost_pa_short_focused_tp_sl_h12.json
ml/reports/price_action/summary.json    ← cross-checks vs fractal backtest
```

### What to inspect first

1. `ml/reports/fractals/fractal_backtest_summary.json`
   — `promising_strategies` lists strategies with ≥200 trades across ≥3 tickers.
   — Check `best_by_profit_factor` and `best_by_cumulative_net`.

2. `ml/reports/price_action/summary.json`
   — Cross-comparison of rule-based vs ML results.
   — `top_price_action_features_by_importance` shows which PA features matter.

3. `ml/reports/price_action/catboost_pa_short_focused_tp_sl_h12.json`
   — `binary_report.SHORT_precision` and `SHORT_f1` are the key metrics.

### First-run result (raw fractal rules)

All four rule-based strategies were unprofitable in the first backtest:

| Strategy | Trades | WinRate | Cum Net | PF |
|----------|--------|---------|---------|-----|
| fractal_breakout_long | ~13,768 | 28% | −2914% | 0.20 |
| fractal_breakdown_short | ~14,563 | 26% | −3211% | 0.19 |
| sweep_reversal_long | ~26,380 | 24% | −5615% | 0.16 |
| sweep_reversal_short | ~23,167 | 26% | −4695% | 0.19 |

**Interpretation:**
Raw fractal signals alone are not profitable. This is expected — most
simple price-action rules have been arbitraged away in liquid markets.
However, this does **not** mean fractal features are useless:

- **Rule-based fractal strategy**: fires a binary signal from a rule.
  Failed here — rules are too simple and produce too many false entries.

- **Fractal features for ML**: adds structural context to a CatBoost model
  alongside 30 technical indicators. The ML model learns non-linear
  combinations of features. Tested in `experiments_price_action.py`.

Each per-strategy report now includes an `inverted_diagnostic` field that
simulates the same entries but in the opposite direction. This checks whether
the rule has "anti-signal" value (if inverted is profitable, the original
rule is consistently wrong and a flipped version may be worth exploring).
Inverted results are diagnostics only — not automatically tradable.

### Promising criteria

A strategy is marked `promising=true` only if **all** of the following hold:

| Criterion | Threshold |
|-----------|-----------|
| Total trades | ≥ 200 |
| Ticker diversity | ≥ 3 tickers |
| Profit factor | > 1.05 |
| Average net return | > 0% |
| Cumulative net return | > 0% |
| Max drawdown | not catastrophic (< 3× cumulative return) |

`rejection_reasons` in each strategy row explains exactly why it failed.

### Why this is research only

- Signals need out-of-sample stability across tickers before use.
- A strategy is considered promising only if it meets all criteria above.
- Profit factor must be consistent across tickers, not driven by one outlier.
- **No APK integration until these criteria are met.**

### Binary short-focused experiment

Experiment 3 (`catboost_pa_short_focused_tp_sl_h12`) trains a binary
CatBoost classifier:
- **Class 1** = SHORT_SETUP (original DOWN label from tp_sl labelling)
- **Class 0** = NOT_SHORT
- Loss function: `Logloss` (binary-compatible)
- Eval metric: `AUC` (binary-compatible)
- No `MultiClass`, `classes_count`, or multiclass-specific metrics

This avoids the CatBoost error that occurs when binary and multiclass
parameters are mixed. Key metrics: `SHORT_precision`, `SHORT_recall`,
`SHORT_f1` in the binary report.

---

## Step 9 — ML signal backtest

After training the price-action CatBoost experiments, verify whether their
generated SHORT/LONG signals have **positive net expectancy** after trading costs.

### Why raw fractal rules failed

All four rule-based strategies in `backtest_fractals.py` were unprofitable:

| Strategy | Profit Factor | Avg Net Return |
|----------|--------------|----------------|
| fractal_breakout_long | 0.20 | −0.21% |
| fractal_breakdown_short | 0.19 | −0.22% |
| sweep_reversal_long | 0.16 | −0.21% |
| sweep_reversal_short | 0.19 | −0.20% |

Simple price-action rules produce too many false entries on liquid MOEX equities.
**Do not further optimise standalone fractal rules.** Fractal and price-action
features may still add value as context features inside an ML model.

### ML SHORT signals — separate backtest

`backtest_ml_signals.py` loads trained model binaries, reproduces the exact
feature pipeline from `experiments_price_action.py` (same labels, same PA
features, same time-split), and runs trades only on the **test split** to
avoid look-ahead bias.

Two models are tested:

| Model | Classifier | Focus |
|-------|-----------|-------|
| `catboost_pa_tp_sl_h12_tp040_sl025_balanced` | Multiclass (DOWN/FLAT/UP) | SHORT + LONG signals |
| `catboost_pa_short_focused_tp_sl_h12` | Binary (SHORT_SETUP vs NOT) | SHORT signals only |

Signal rules:

- **Multiclass**: SHORT if P(DOWN) ≥ threshold and P(DOWN) > P(UP); LONG if P(UP) ≥ threshold and P(UP) > P(DOWN)
- **Binary short**: SHORT if P(SHORT_SETUP=1) ≥ threshold

Thresholds tested: 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75

### Conservative TP/SL assumptions

| Assumption | Value |
|-----------|-------|
| Horizon | 12 candles (5m bars) |
| Take profit (SHORT) | future low ≤ entry × (1 − 0.40%) |
| Stop loss (SHORT) | future high ≥ entry × (1 + 0.25%) |
| Same-candle both hit | SL wins (conservative) |
| Neither hit | exit at horizon-bar close |
| Commission | 5 bps per leg |
| Slippage | 5 bps per leg |
| Round-trip cost | 0.20% (2 × 10 bps) |

### First run — save models, then backtest

```
# Step 1: train experiments and save model binaries
python ml\experiments_price_action.py

# Step 2: backtest all models across all thresholds
python ml\backtest_ml_signals.py --all

# Or backtest a single model
python ml\backtest_ml_signals.py --model catboost_pa_tp_sl_h12_tp040_sl025_balanced
python ml\backtest_ml_signals.py --model catboost_pa_short_focused_tp_sl_h12
```

If model binaries are missing you will see:
```
Model file not found. Run python ml\experiments_price_action.py first.
```

### Outputs

```
ml/reports/ml_signal_backtests/
  catboost_pa_tp_sl_h12_tp040_sl025_balanced_backtest.json
  catboost_pa_short_focused_tp_sl_h12_backtest.json
  summary.json      ← start here
```

`summary.json` contains:

- `overall_conclusion` — clear statement on whether any setup is profitable
- `best_by_profit_factor` — best setup by PF among those with ≥200 trades
- `best_by_cumulative_net_return` — best setup by total return
- `best_short_setup` — best setup with ≥200 SHORT trades by PF
- `all_promising_setups` — list of setups meeting all criteria
- `rejection_reasons_by_setup` — why each setup failed (if any failed)

Per-model reports include for each threshold:
- total/long/short signal counts
- win rate, avg gross/net return, median net return
- cumulative net return, profit factor, max drawdown
- best/worst trade, avg holding candles
- take_profit / stopped_out / horizon_exit counts
- per-ticker and monthly breakdowns
- LONG-only and SHORT-only side metrics

### Promising criteria

A model+threshold setup is marked `promising=true` only when **all** hold:

| Criterion | Threshold |
|-----------|-----------|
| Total trades | ≥ 200 |
| SHORT trades | ≥ 200 (primary focus is SHORT side) |
| Profit factor | > 1.05 |
| Average net return | > 0% |
| Cumulative net return | > 0% |
| Ticker concentration | ≤ 50% in any single ticker |
| Ticker diversity | ≥ 3 tickers with trades |

If **no setup is promising**, the summary says so explicitly.
Do not force a positive conclusion.

### APK integration policy

APK integration is on hold until a backtest setup meets all promising criteria
with consistent performance across tickers and months. Classification metrics
alone (precision, F1) are insufficient — positive net expectancy after costs
is required.

---

## Disclaimer

Experimental research model only. Not financial advice.
Past performance on historical data does not guarantee future results.
