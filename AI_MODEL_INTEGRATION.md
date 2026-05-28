# AI Model Integration — pa_short_v0

## Overview

The app includes an experimental local price-action SHORT signal based on a CatBoost binary classifier.
All inference runs **locally in the browser / WebView** — no backend, no cloud API.

## Status

| Item | Value |
|---|---|
| Model | `catboost_pa_short_focused_tp_sl_h12` |
| Exported ID | `pa_short_v0` |
| Type | Binary classifier: SHORT_SETUP vs NOT_SHORT |
| Features | 52 (30 technical + 22 price-action) |
| Horizon | 12 candles (5-minute timeframe) |
| TP / SL | 0.40% / 0.25% |
| Backtest validated | **NO** |
| Passed promising criteria | **NO** |
| Best profit factor (offline) | 0.3421 |
| Inference location | Local (TypeScript, no backend) |
| Candle persistence | None |
| Prediction persistence | None |

## Research-only warning

**This model has NOT passed any profitable backtest.**
The best offline profit factor was 0.34 (well below 1.0).
Average and cumulative net returns were negative.

All UI labels reflect this:
- Panel title: "Experimental AI"
- Risk levels: none / watch / elevated / high (not "buy" / "sell")
- Warning always visible: *"Research only. Backtest has not shown positive net expectancy."*
- Toggle label: "PA SHORT" (not "Recommended" or "Signal")

## File map

| File | Purpose |
|---|---|
| `ml/export_app_model.py` | Export `.cbm` → `model.json` + `manifest.json` |
| `ml/models/catboost_pa_short_focused_tp_sl_h12.cbm` | Source model (gitignored) |
| `frontend/public/models/pa_short_v0/model.json` | CatBoost JSON (0.83 MB) |
| `frontend/public/models/pa_short_v0/manifest.json` | Feature names, thresholds, metadata |
| `frontend/src/ml/catboostJsonRuntime.ts` | CatBoost JSON evaluator (symmetric trees) |
| `frontend/src/ml/priceActionFeatures.ts` | 52-feature calculation matching Python pipeline |
| `frontend/src/ml/paShortSignal.ts` | Load → compute → return `PaShortSignalResult` |
| `frontend/src/components/mobile/AiSignalPanel.tsx` | Panel with Mock/PA SHORT toggle |
| `frontend/src/pages/MobileChartPage.tsx` | PA model init, debounced recompute |

## How inference works

1. On app mount, `initPaModel()` fetches `manifest.json` and `model.json` asynchronously.
2. Feature names in the manifest are checked against the TypeScript `PA_FEATURE_NAMES` constant.
   If they differ, the model shows "Experimental model unavailable: feature mismatch".
3. On every candle update (debounced 300 ms), `computePaShortSignal(candles)` is called:
   - `calculatePaFeatures(candles)` — computes all 52 features from the in-memory candle array
   - `evaluateCatBoostBinary(model, features)` — runs symmetric-tree traversal, returns P(SHORT)
   - Risk level mapped from probability:
     - < 0.55 → none
     - 0.55–0.65 → watch
     - 0.65–0.70 → elevated
     - ≥ 0.70 → high

## Feature parity

The 52 features are calculated in TypeScript to match the Python training pipeline:

- **Returns**: `pct_change * 100` (in %)
- **Volatility**: rolling std of log returns × 100
- **Candle shape**: normalized by open/range × 100
- **Volume**: `(vol / mean5 - 1) * 100` and z-score
- **SMA/EMA**: `(close / ma - 1) * 100`; slopes = `(ma[t] / ma[t-5] - 1) * 100`
- **RSI**: Wilder EWM (`com=13`)
- **MACD**: EMA12/EMA26 difference normalized by close × 100; signal = EMA9 of MACD
- **ATR**: Wilder EWM (`com=13`) of true range, normalized by close × 100
- **Bollinger**: position in [0,1], width = band_range / SMA × 100
- **Fractals**: confirmed only after `right_span=2` future bars — no leakage

If any feature is NaN or Infinity (e.g., insufficient candle history, no confirmed fractals),
the signal returns "unavailable" with a descriptive reason.

## How to regenerate the model export

If the model is retrained, re-run the export:

```
python ml\experiments_price_action.py --experiments 3
python ml\export_app_model.py
```

Then review `frontend/public/models/pa_short_v0/model.json` (must be ≤ 3 MB) and commit only:

```
frontend/public/models/pa_short_v0/model.json
frontend/public/models/pa_short_v0/manifest.json
```

**Do NOT commit `.cbm` files.**

## How to rebuild the APK

```
cd frontend
npm.cmd run build
npm.cmd run android:sync
cd android
.\gradlew.bat assembleDebug
```

APK output: `frontend\android\app\build\outputs\apk\debug\app-debug.apk`

Rename and upload to GitHub Releases as: `technical-analyst-v1.0.10.apk`

## Release

- **Release tag**: `v1.0.10`
- **APK asset name**: `technical-analyst-v1.0.10.apk`
- **versionCode**: 11
- **versionName**: 1.0.10
