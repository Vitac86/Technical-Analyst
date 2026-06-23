# Strategy Lab v1.7 — MT5 Signal-Only Bridge

> **v1.7 is signal-only. Execution is intentionally disabled.**
> The bridge **never** opens, closes or modifies orders/positions, never logs in
> to the broker, never stores credentials, and never enables live trading.
> `execution_enabled` is always `false`.

The bridge connects to a **locally running** MetaTrader 5 terminal, reads a
Strategy Lab v1.6 exported strategy config, pulls recent candles, computes the
**exact same** rule-based signal as the backtester (by reusing
`presets` / `strategies` / `indicators` — no duplicated logic), and writes
alerts/logs. It is a research/monitoring tool, not a trading robot.

The primary production candidate is finalist **D**: H4 long-only SuperTrend with
an ATR trailing stop and risk-percent sizing. Finalist **C** (H1 Donchian
breakout) is also supported.

## Files

| File | Role |
| --- | --- |
| [mt5_signal_bridge.py](mt5_signal_bridge.py) | Core: config validation, MT5 connection, rates→DataFrame, closed-candle rule, signal generation, safety locks. |
| [run_mt5_signal_bridge.py](run_mt5_signal_bridge.py) | CLI runner (`--once` / polling). |
| [signal_store.py](signal_store.py) | Local state + signal log + latest signal (JSON/CSV). |
| [../api/v1/endpoints/strategy_lab_signals.py](../api/v1/endpoints/strategy_lab_signals.py) | Read-only `GET /api/strategy-lab/signals/latest` and `/history`. |

## 1. Export a config from the UI

In the Strategy Lab UI, pick a preset (default **D**), adjust parameters if you
like, then use **Export config** (the `POST /api/strategy-lab/export-config`
endpoint). Save the downloaded JSON, e.g. `d_config.json`.

The exported config already sets the fields the bridge validates:

```jsonc
{
  "strategy_id": "D_supertrend_h4_trailing_risk",
  "symbol": "XAUUSD",
  "timeframe": "H4",
  "direction_mode": "long_only",
  "strategy_parameters": { "atr_period": 10, "multiplier": 2.0 },
  "exit_parameters": { "initial_stop_loss_atr": 2.5, "trailing_stop_atr": 6.0, "take_profit_atr": null },
  "risk_parameters": { "risk_percent": 1.0, "leverage": 50.0, "initial_equity": 10000.0 },
  "stop_atr_period": 10,
  "ml_filter_enabled": false
}
```

The bridge **rejects** the config unless `ml_filter_enabled` is `false`,
`direction_mode` is `long_only`, the `strategy_id` is supported, and the
timeframe matches the strategy (H4 for D, H1 for C).

## 2. Install the MT5 package

```bash
pip install MetaTrader5
```

If it is missing, the bridge fails with exactly that hint. Open and log in to the
MT5 terminal yourself — the bridge attaches to the running terminal and never
handles your login.

## 3. Run once

```bash
python backend/app/strategy_lab/run_mt5_signal_bridge.py \
    --config path/to/d_config.json --once
```

If your broker names gold `XAUUSDrfd`, the bridge resolves it automatically; you
can also force it with `--symbol XAUUSDrfd`.

## 4. Run in polling mode

```bash
python backend/app/strategy_lab/run_mt5_signal_bridge.py \
    --config path/to/d_config.json --poll-seconds 60
```

It checks every `--poll-seconds` (default 60) and emits at most **one signal per
closed candle**. On an unchanged candle it prints
`No new closed candle / already processed`. Stop with Ctrl-C.

### CLI options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--config` | *(required)* | Path to the exported v1.6 config JSON. |
| `--bars` | `500` | Candles to fetch (last one is treated as forming). |
| `--once` | off | Run a single check and exit. |
| `--poll-seconds` | `60` | Polling interval when not `--once`. |
| `--symbol` | config symbol | Override with the broker's exact MT5 symbol. |
| `--dry-run` / `--no-dry-run` | `true` | Reserved safety flag; v1.7 never executes regardless. |
| `--output-dir` | `MetaTrader_Data/reports/mt5_signal_bridge/` | Where logs/state are written. |

## 5. Where logs are written

Under `--output-dir` (default `MetaTrader_Data/reports/mt5_signal_bridge/`,
which is **git-ignored** — generated logs are never committed):

- `state.json` — last processed `signal_time` + `signal_id` per
  strategy/symbol/timeframe (deduplication).
- `signals.csv` — append-only log, one row per processed closed candle.
- `latest_signal.json` — the most recent signal record.

Each signal record contains: `signal_id`, `generated_at`, `symbol`, `timeframe`,
`strategy_id`, `signal_time`, `signal_type` (`BUY`/`NONE`), `reason`,
`close_price`, `atr_value`, `suggested_entry_reference`
(`next_bar_open_or_market`), `risk_percent`, `initial_stop_loss_atr`,
`trailing_stop_atr`, `take_profit_atr`, `status` (`signal_only`), and
`execution_enabled` (`false`).

### Read them via the API (optional)

The FastAPI backend exposes read-only endpoints that read these files (the
bridge process is independent and does **not** need to run inside FastAPI):

- `GET /api/strategy-lab/signals/latest`
- `GET /api/strategy-lab/signals/history?limit=50`

Point the API at a custom directory with the `MT5_SIGNAL_BRIDGE_DIR` env var (it
must match the bridge's `--output-dir`).

## 6. Verify signals against Strategy Lab

The bridge calls `presets.generate_signals(...)` — the identical function the
backtester uses — and applies the closed-candle rule (it evaluates the
**second-to-last** fetched bar, never the live candle). To verify:

1. Note a `signal_time` and `signal_type` from `signals.csv` / `latest_signal.json`.
2. Run the same preset in the Strategy Lab backtester over the matching period.
3. A `BUY` at `signal_time T` corresponds to a backtester entry at the **open of
   the next bar** after `T` (`suggested_entry_reference = next_bar_open_or_market`).

## 7. How to verify the bridge is signal-only

- `execution_enabled` is `false` in every record, every CSV row, and both API
  responses.
- The module-level lock `EXECUTION_ENABLED = False` and the runtime guard
  `assert_signal_only()` (called before every evaluation) enforce it.
- A test (`test_no_order_execution_functions_referenced`) asserts that no
  order/trade-mutating MT5 call (`order_send`, `order_check`, `order_modify`,
  `order_close`, `position_close`, `TRADE_ACTION`, …) appears anywhere in the
  bridge package. There are **no** order-execution API endpoints.

Run the tests (no MT5 terminal required — MT5 is mocked):

```bash
cd backend
.venv/Scripts/python.exe -m pytest app/tests/test_mt5_signal_bridge.py -q
```
