"""Strategy Lab: a lightweight backtesting core for MT5 historical data.

A self-contained, dependency-light (pandas/numpy only) research toolkit for
evaluating rule-based trading strategies on exported MetaTrader 5 CSV data.

The package is intentionally decoupled from the FastAPI application: it does
not import the app, touch the database, or change any existing behaviour. It is
meant to be driven from :mod:`app.strategy_lab.run_backtests` or imported
directly for ad-hoc research.

Version 1 scope:
    * fixed/bar spread cost modelling
    * ATR-based stop-loss / take-profit exits
    * single position at a time, long and short
    * no machine learning, no UI

Version 1.2 scope (research only, still pandas/numpy and no ML/UI):
    * account-based backtester with equity, position sizing and lots
    * leverage / margin model with stop-out (forced liquidation)
    * wide SL/TP, ATR trailing, time and opposite-signal exits

Version 1.3 scope (post-processing of the v1.2 outputs; still no ML/UI):
    * effective leverage (real exposure vs. equity) vs. nominal broker leverage
    * leverage comparison proving when PnL actually changes with leverage
    * yearly + fixed-split walk-forward robustness and return-concentration
    * separate candidate rankings (driven by run_robustness_diagnostics)

Version 1.4 scope (deterministic finalist confirmation; still no ML/UI):
    * exhaustive, non-random re-test of the shortlisted finalists over dense
      local parameter grids (driven by run_finalist_confirmation)
    * per-side execution slippage in the risk backtester (worsens both fills)
    * Base / Conservative / Stress cost scenarios and cost-sensitivity scoring
    * train / test / walk-forward splits with a research-only confirmation_score

Version 1.5 scope (ML signal *filter* only -- never predicts price or generates
trades; driven by run_ml_signal_filter):
    * a leakage-safe dataset of executed rule-based trade candidates, with
      features taken at the signal candle and realised outcomes as targets
      (ml_features, ml_signal_filter)
    * a CatBoost classifier that only filters existing rule-based long signals,
      with the probability threshold chosen on validation data only and the
      held-out test period used purely for the filtered-vs-unfiltered comparison
"""

__all__ = [
    "data_loader",
    "indicators",
    "strategies",
    "backtester",
    "metrics",
    "exit_research",
    "risk_backtester",
    "ml_features",
    "ml_signal_filter",
]
