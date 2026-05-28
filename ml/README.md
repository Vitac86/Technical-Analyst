# ML Training Scaffold — Technical Analyst

Planned offline training pipeline for a CatBoost direction-probability model.
This directory is for PC-side tooling only — nothing here is required for the APK build.

---

## Planned pipeline

### 1. Download historical MOEX candles (PC)

Fetch raw candle data from MOEX ISS for target instruments and timeframes.
Store locally as CSV or Parquet for feature engineering.

### 2. Build features

Use the same feature definitions as `frontend/src/ml/features.ts` to avoid
train/serve skew. Port or mirror the TypeScript logic to Python:

```
return_1, return_3, return_5, return_10
volatility_10, volatility_20
candle_body_pct, candle_range_pct
upper_wick_pct, lower_wick_pct
volume_change_5, volume_zscore_20
price_vs_sma_20, price_vs_ema_20
sma_20_slope, ema_20_slope
high_low_position_20
```

Feature order must match `AI_FEATURE_NAMES` in `frontend/src/ml/types.ts`.

### 3. Create labels

Label each candle with `UP / DOWN / FLAT` based on the close price
`horizonCandles` (currently 3) candles ahead:

- `UP`   if forward_return > +threshold
- `DOWN` if forward_return < -threshold
- `FLAT` otherwise

Use a conservative threshold (e.g. 0.5–1× average volatility).
Exclude the last `horizonCandles` rows per series (no future leakage).

### 4. Train CatBoost MultiClass model

```python
from catboost import CatBoostClassifier

model = CatBoostClassifier(
    iterations=500,
    learning_rate=0.05,
    depth=6,
    loss_function='MultiClass',
    classes_count=3,       # DOWN=0, FLAT=1, UP=2
    eval_metric='Accuracy',
    random_seed=42,
)
model.fit(X_train, y_train, eval_set=(X_val, y_val), early_stopping_rounds=50)
```

### 5. Validate with walk-forward split

Use expanding-window or rolling-window walk-forward to avoid look-ahead bias.
Evaluate Accuracy, log-loss, and calibration of predicted probabilities.
Reject model if no-trade rate exceeds 80% or accuracy < naive baseline.

### 6. Export model artifact

Export a small model for APK bundling (target < 2 MB):

```python
model.save_model('model_direction_v2.cbm')
```

Consider `model_shrink_rate` and feature selection to keep size small.
Document the exact feature list and order in `model-manifest.json`.

### 7. Integrate model artifact into APK

1. Copy `model_direction_v2.cbm` to `frontend/public/models/`.
2. Update `frontend/public/models/model-manifest.json` with new version and metadata.
3. Add a real CatBoost WASM/JS runtime in `frontend/src/ml/mockModel.ts`
   (replace the `runMockModel` function — keep the same `FeatureVector` input contract).
4. Bump `modelVersion` to `"catboost_direction_v2"`.
5. Rebuild APK.

---

## File layout (future)

```
ml/
  README.md          ← this file
  data/              ← raw candle CSVs (gitignored)
  notebooks/         ← exploration notebooks (gitignored)
  train.py           ← training entry point
  features.py        ← mirrors frontend/src/ml/features.ts
  evaluate.py        ← walk-forward validation
  models/            ← exported .cbm artifacts (gitignored until ready)
```

---

## Notes

- Do NOT add Python dependencies to `frontend/`.
- Do NOT make the frontend build depend on Python.
- Do NOT commit trained model binaries until size and accuracy are validated.
- Keep feature names in sync between `features.py` and `frontend/src/ml/features.ts`.
