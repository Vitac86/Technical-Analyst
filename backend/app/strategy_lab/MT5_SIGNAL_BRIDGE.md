# Strategy Lab v1.7 / v1.7.1 — MT5 Signal-Only Bridge

> **Signal-only. Execution is intentionally disabled.**
> The bridge **never** opens, closes or modifies orders/positions, never logs in
> to the broker, never stores credentials, and never enables live trading.
> `execution_enabled` is always `false`.

**v1.7.1** adds a UI control panel (and the backend API behind it) so you can
drive the *same* signal-only bridge from the Strategy Lab page instead of typing
long CLI commands — save a config, check MT5 readiness, run one check, and
start/stop polling, all from the browser. The CLI remains as a fallback.

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
| [mt5_bridge_manager.py](mt5_bridge_manager.py) | v1.7.1 control layer: save/list configs, MT5 readiness, run-once, start/stop polling subprocess, tail logs. Reuses the bridge core — no duplicated logic. |
| [../api/v1/endpoints/strategy_lab_signals.py](../api/v1/endpoints/strategy_lab_signals.py) | Read-only `/latest` + `/history` **and** the v1.7.1 control endpoints. |

## Using the bridge from the UI (v1.7.1)

Open **Strategy Lab** in the web app and scroll to the **MT5 Signal Bridge**
panel below the backtest. The flow is top-to-bottom:

1. **Config** — pick/adjust a preset (default **D**) as usual, then click
   **Save current config for bridge**. This exports the current config and saves
   it server-side under `MetaTrader_Data/configs/`. The saved file appears in the
   **Saved configs** dropdown; use **Refresh configs** to reload the list.
   *Until a config is saved, **Check once** and **Start polling** are disabled and
   the panel shows “Save current config for bridge first.”*
2. **MT5 readiness** — click **Check MT5 connection**. A badge shows
   **Ready / Warning / Error** plus terminal/account/symbol/timeframe, whether
   rates are available, and the latest closed-candle time. If MT5 is missing it
   tells you to *install MetaTrader5 in the backend venv and open / log in to MT5*.
3. **Signal actions** — **Check once** runs a single check now; **Start polling**
   launches a background poller every *poll seconds* (default 60); **Stop polling**
   stops it. While polling is running, **Start** is disabled and **Stop** is
   enabled (and vice-versa).
4. **Latest signal** / **Signal history** — show the most recent alert and the
   full log. A prominent **“Signal-only mode. Execution disabled.”** badge is
   always visible, and `execution_enabled` is shown as `false` on every row.
5. **Logs** — collapsed by default; **Refresh logs** shows the poller’s
   stdout/stderr tail.

### Control API (all under `/api/strategy-lab/signals`, no execution)

| Method & path | Purpose |
| --- | --- |
| `GET  /latest` | Most recent emitted signal. |
| `GET  /history?limit=50` | Recent signals, newest first. |
| `POST /configs/save` | Validate + save a config JSON (body: `{config, name?}`). |
| `GET  /configs` | List saved configs with a summary. |
| `POST /mt5-check` | MT5 readiness for `{config_path \| config, bars}` — no signal, no trade. |
| `POST /check-once` | Run one signal-only check; writes via the store. |
| `POST /start` | Start polling subprocess (`{config_path, poll_seconds, bars}`). |
| `POST /stop` | Stop the managed polling subprocess. |
| `GET  /status` | Running flag, pid, started_at, config, poll_seconds, latest signal, log excerpts. |
| `GET  /logs?lines=100` | stdout/stderr tail. |

There are **no** order-execution endpoints. Every response includes
`execution_enabled: false`.

### How start/stop works (process management)

`POST /start` launches the **existing CLI runner** as a subprocess using the
backend’s own interpreter:

```text
<sys.executable> run_mt5_signal_bridge.py --config <saved.json> \
    --poll-seconds <n> --bars <n> --output-dir <reports/mt5_signal_bridge>
```

stdout/stderr are redirected to `bridge_stdout.log` / `bridge_stderr.log`, and
the pid + metadata are written to `bridge_process.json`. A second `start` while a
recorded pid is still alive is refused (no duplicate poller). `POST /stop`
terminates that pid gracefully (`taskkill /T` on Windows, `SIGTERM` on POSIX) and
force-kills only if it does not exit; liveness is checked via the Win32 API on
Windows (never via `os.kill`, which would terminate the process). Because state
lives in `bridge_process.json` + the log files, `GET /status` is correct even
after a backend restart.

### Manual CLI fallback

The UI is just a convenience wrapper. You can still run the bridge by hand
(useful for debugging or headless boxes) — see sections 3–4 below. The UI-saved
configs live in `MetaTrader_Data/configs/`, so you can point `--config` straight
at one of them.

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
- `bridge_process.json` — managed-poller state (pid, started_at, config_path,
  poll_seconds, bars, status). *(v1.7.1)*
- `bridge_stdout.log` / `bridge_stderr.log` — the polling subprocess output.
  *(v1.7.1)*

UI-saved configs are written to `MetaTrader_Data/configs/` (also **git-ignored** —
generated configs are never committed). All of `mt5_exports/`, `reports/` and
`configs/` under `MetaTrader_Data/` are ignored by git.

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
.venv/Scripts/python.exe -m pytest app/tests/test_mt5_signal_bridge.py \
    app/tests/test_mt5_bridge_manager.py -q
```

`test_mt5_bridge_manager.py` additionally asserts none of the order/trade tokens
appear in the new manager **or** the control endpoints, and that the start
endpoint never launches a duplicate poller.

## 8. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| **MT5 readiness: “Install MetaTrader5…”** | The `MetaTrader5` package is not in the backend venv. `pip install MetaTrader5` (Windows only) and restart the backend. |
| **`mt5.initialize() failed`** | The terminal is not running / not logged in. Open MetaTrader 5 and log in to your broker account, then re-check. The bridge attaches to the running terminal and never handles credentials. |
| **`Symbol 'XAUUSD' not found`** | Your broker uses a different name. The bridge auto-tries the `…rfd` variant; otherwise re-export/save the config with the broker’s exact symbol (e.g. `XAUUSDrfd`). |
| **`copy_rates_from_pos returned no data`** | The symbol is not in Market Watch or the history is empty. Add the symbol in MT5 (right-click → Show) and let it download history. |
| **Readiness shows “Warning: only N closed bars”** | Not enough history yet for the indicators. Let MT5 download more bars, or lower expectations — a check can still run, but verify the signal carefully. |
| **Check once / Start polling disabled** | No config saved. Click **Save current config for bridge** first. |
| **Status shows stopped right after Start** | The poller exited — open **Logs** (or `bridge_stderr.log`) for the reason (usually MT5 not running or symbol/rates issues). |

## 9. Quick commands

```bash
# 1. Install the MT5 package (Windows; the terminal must be installed + logged in)
pip install MetaTrader5

# 2. Start the backend
cd backend
.venv/Scripts/python.exe -m uvicorn app.main:app --reload

# 3. Start the frontend (separate shell) and open Strategy Lab
cd frontend
npm run dev          # then browse to the app and open "Strategy Lab"

# 4. In the UI: Save current config for bridge → Check MT5 connection →
#    Check once → Start/Stop polling. Latest signal + history update live.

# 5. CLI fallback (uses a UI-saved config or any exported config JSON)
python backend/app/strategy_lab/run_mt5_signal_bridge.py \
    --config MetaTrader_Data/configs/D_supertrend_h4_trailing_risk_XAUUSD_H4.json \
    --poll-seconds 60
```
