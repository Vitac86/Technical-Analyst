# Strategy Lab v1.7.x — MT5 Signal-Only Bridge

> **Signal-only. Execution is intentionally disabled.**
> The bridge **never** opens, closes or modifies orders/positions, never logs in
> to the broker, never stores credentials, and never enables live trading.
> `execution_enabled` is always `false`. Everything in the **trading plan**
> below — entry, stop, take-profit, suggested lot — is a labelled *reference*,
> not an order instruction. **No order is ever sent.**

**v1.7.1** adds a UI control panel (and the backend API behind it) so you can
drive the *same* signal-only bridge from the Strategy Lab page instead of typing
long CLI commands — save a config, check MT5 readiness, run one check, and
start/stop polling, all from the browser. The CLI remains as a fallback.

**v1.7.2** enriches the output so the panel is *actionable* without ever
executing. Each signal now carries a structured **`trading_plan`** (reference
entry, initial stop, trailing-stop reference, take-profit, risk distance, risk
amount and a **suggested lot reference** for the configured `risk_percent` and
account equity), a **`market_snapshot`** (OHLC + spread + candle times) and a
**`strategy_state`** (regime, SuperTrend/Donchian levels, fresh-signal flag). A
new **`recent_checks`** feed reports the latest N closed candles so you can see
*what happened over the last several candles*, not just the latest one — while
still emitting **exactly one official signal per closed candle**. It remains
signal-only: there are still no order/execution endpoints anywhere.

**v1.7.3** adds explicit **Next BUY condition** and **Distance to BUY zone**
diagnostics. For D, the current SuperTrend value is exposed as a reference
boundary while the regime is bearish or neutral, together with price, ATR and
percentage distance. For C, the equivalent reference is the Donchian breakout
high. These values explain the latest closed candle; they do not predict or
guarantee the next trigger.

**v1.7.4** makes the D diagnostics robust for every closed candle. The raw
SuperTrend value is normalized to `supertrend_value`, `buy_zone_level` always
matches it when available, and both latest-signal and recent-check payloads
carry the same boundary, relation and distance fields. In the UI,
`buy_zone_level` is displayed as **Current SuperTrend boundary**.

The bridge connects to a **locally running** MetaTrader 5 terminal, reads a
Strategy Lab v1.6 exported strategy config, pulls recent candles, computes the
**exact same** rule-based signal as the backtester (by reusing
`presets` / `strategies` / `indicators` — no duplicated logic), and writes
alerts/logs. It is a research/monitoring tool, not a trading robot.

The primary production candidate is finalist **D**: H4 long-only SuperTrend with
an ATR trailing stop and risk-percent sizing. Finalist **C** (H1 Donchian
breakout) is also supported. The entire v1.7.x bridge is **long-only**: it emits
`BUY` or `NONE` and does not emit a SELL/SHORT signal.

## Files

| File | Role |
| --- | --- |
| [mt5_signal_bridge.py](mt5_signal_bridge.py) | Core: config validation, MT5 connection, rates→DataFrame, closed-candle rule, signal generation, safety locks. |
| [run_mt5_signal_bridge.py](run_mt5_signal_bridge.py) | CLI runner (`--once` / polling). |
| [signal_store.py](signal_store.py) | Local state + signal log + latest signal (JSON/CSV). |
| [mt5_bridge_manager.py](mt5_bridge_manager.py) | v1.7.1 control layer: save/list configs, MT5 readiness, run-once, start/stop polling subprocess, tail logs. Reuses the bridge core — no duplicated logic. |
| [../api/v1/endpoints/strategy_lab_signals.py](../api/v1/endpoints/strategy_lab_signals.py) | Read-only `/latest` + `/history` **and** the v1.7.1 control endpoints. |

## Using the bridge from the UI (v1.7.3)

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
4. **Latest signal** — three cards: a **Signal status** card with a big
   **BUY / NO ENTRY** badge, the human-readable reason, the strategy regime and a
   fresh-signal flag; a **Market snapshot** card (close, ATR, spread, candle
   times, the SuperTrend reference boundary or Donchian breakout level, distance
   in price/ATR/percent, and the above/below/at relation); and a **Trading plan
   (reference only)** card. For a BUY the plan shows the human reason, reference
   entry, initial stop, trailing-stop reference, take-profit (or “none”), risk
   distance, risk amount, **suggested lot reference** and account-equity
   reference. For NO ENTRY it shows a dedicated **Next BUY condition** block,
   the human reason and current distance — never a fabricated entry price.
5. **Recent checks** — a table of the last ~10–20 closed candles (time, close,
   ATR, regime, BUY/NO ENTRY, buy-zone level, compact price/ATR distance,
   relation and human-readable reason) so you can see what happened over the
   last several candles, not only the latest.
6. **Signal history** — the official one-row-per-closed-candle log (generated_at,
   signal time, signal, reason, close, reference entry, initial stop, suggested
   lot, exec). A prominent **“Signal-only mode. Execution disabled.”** badge is
   always visible and `execution_enabled` is `false` on every row.
7. **Logs** — collapsed by default; **Refresh logs** shows the poller’s
   stdout/stderr tail.

### Control API (all under `/api/strategy-lab/signals`, no execution)

| Method & path | Purpose |
| --- | --- |
| `GET  /latest` | Most recent emitted (enriched) signal. |
| `GET  /history?limit=50` | Recent signals, newest first (flattened CSV rows). |
| `GET  /recent-checks?limit=20` | Per-candle diagnostics over the latest closed candles (newest first). *(v1.7.2)* |
| `POST /configs/save` | Validate + save a config JSON (body: `{config, name?}`). |
| `GET  /configs` | List saved configs with a summary. |
| `POST /mt5-check` | MT5 readiness for `{config_path \| config, bars}` — no signal, no trade. |
| `POST /check-once` | Run one signal-only check; writes via the store. Returns the enriched signal **and** `recent_checks`. Body also accepts `recent_limit` (default 10, max 100). |
| `POST /start` | Start polling subprocess (`{config_path, poll_seconds, bars}`). |
| `POST /stop` | Stop the managed polling subprocess. |
| `GET  /status` | Running flag, pid, started_at, config, poll_seconds, latest signal, **recent checks**, log excerpts. |
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
| `--recent-limit` | `10` | Closed candles of diagnostics to record per check (max 100). *(v1.7.2)* |
| `--symbol` | config symbol | Override with the broker's exact MT5 symbol. |
| `--dry-run` / `--no-dry-run` | `true` | Reserved safety flag; v1.7 never executes regardless. |
| `--output-dir` | `MetaTrader_Data/reports/mt5_signal_bridge/` | Where logs/state are written. |

## 5. Where logs are written

Under `--output-dir` (default `MetaTrader_Data/reports/mt5_signal_bridge/`,
which is **git-ignored** — generated logs are never committed):

- `state.json` — last processed `signal_time` + `signal_id` per
  strategy/symbol/timeframe (deduplication).
- `signals.csv` — append-only log, one row per processed closed candle
  (flattened key + trading-plan columns; see below).
- `latest_signal.json` — the most recent **enriched** signal record (full
  `market_snapshot` / `strategy_state` / `trading_plan` objects).
- `recent_checks.json` — per-candle diagnostics over the latest N closed candles
  (a display aid; it emits no official signal). *(v1.7.2)*
- `bridge_process.json` — managed-poller state (pid, started_at, config_path,
  poll_seconds, bars, status). *(v1.7.1)*
- `bridge_stdout.log` / `bridge_stderr.log` — the polling subprocess output.
  *(v1.7.1)*

UI-saved configs are written to `MetaTrader_Data/configs/` (also **git-ignored** —
generated configs are never committed). All of `mt5_exports/`, `reports/` and
`configs/` under `MetaTrader_Data/` are ignored by git.

### The enriched signal record (v1.7.3)

Top-level identity fields: `signal_id`, `generated_at`, `symbol`, `timeframe`,
`strategy_id`, `signal_time`, `signal_type` (`BUY`/`NONE`), `reason`,
`reason_human`, `strategy_regime`, `status` (`signal_only`) and
`execution_enabled` (always `false`). The legacy flat fields
(`close_price`, `atr_value`, `suggested_entry_reference`, `risk_percent`,
`initial_stop_loss_atr`, `trailing_stop_atr`, `take_profit_atr`) are kept for
back-compat. The new value is in three nested objects:

**`market_snapshot`** — `close_price`, `open_price`, `high_price`, `low_price`,
`atr_value`, `spread_points` (when MT5 provides it), `latest_closed_candle_time`,
`previous_closed_candle_time`.

**`strategy_state`** — `strategy_regime` (`bullish`/`bearish`/`neutral`/
`unknown`), `previous_strategy_regime`, `is_new_long_signal`,
`bars_since_last_long_signal`, plus the indicator levels: `supertrend_value` and
`supertrend_distance_atr` for **D**, or `donchian_high`, `donchian_low` and
`donchian_position` for **C**. It also includes:

- `next_buy_condition` — the closed-candle rule required for the next fresh BUY.
- `buy_zone_level` — D's current SuperTrend reference boundary whenever the
  indicator value is available, or C's Donchian breakout high. For D the UI
  displays this field as **Current SuperTrend boundary**.
- `distance_to_buy_zone_price` — non-negative price distance to that reference.
- `distance_to_buy_zone_atr` — the same distance divided by the current ATR.
- `distance_to_buy_zone_pct` — the same distance as a percentage of close.
- `buy_zone_relation` — `below_buy_zone`, `above_buy_zone`, `at_buy_zone`, or
  `unknown`.

For **D**, the SuperTrend boundary comes from the latest fully closed H4 candle.
It is a **current reference boundary**, not a guaranteed trigger price:
SuperTrend can move when future candles close. A BUY requires a fresh bullish
flip; an already-bullish regime does not repeat the entry. For **C**, a BUY
requires a fully closed H1 candle to break above the Donchian high used by the
strategy.

`supertrend_distance_atr` is the absolute distance
`abs(close_price - supertrend_value) / atr_value`.
`signed_distance_to_supertrend_atr` keeps direction:
`(close_price - supertrend_value) / atr_value`.

**`trading_plan`** — a labelled **reference**, never an order:

| Field | Meaning |
| --- | --- |
| `reference_entry_type` | Always `next_bar_open_or_market_reference`. |
| `reference_entry_price` | The **reference** entry. Because the bridge evaluates the *closed* candle, it uses that candle’s close as a conservative reference — the live next-bar open / actual fill is **not guaranteed**. `null` when there is no entry. |
| `initial_stop_price` | `reference_entry_price − initial_stop_loss_atr × atr_value`. `null` for NONE. |
| `trailing_stop_reference` | **D only**: `close_price − trailing_stop_atr × atr_value`. For NONE it is only shown when the regime is already bullish (informational); otherwise `null`. C uses a fixed stop, so this is `null`. |
| `take_profit_price` | `reference_entry_price + take_profit_atr × atr_value`, or `null` when `take_profit_atr` is `null` (D’s default) or there is no entry. |
| `risk_per_unit` | `reference_entry_price − initial_stop_price` (price distance at risk per unit). |
| `risk_percent` | The configured risk per trade. |
| `account_equity_reference` / `account_equity_source` | MT5 `account_info.equity` when available (`mt5_account_equity`); otherwise the config’s `initial_equity` (`config_initial_equity`); otherwise `unavailable`. |
| `risk_amount` | `account_equity_reference × risk_percent / 100`. |
| `suggested_lot` | **A sizing reference, not an order:** `risk_amount / (risk_per_unit × contract_size)`, rounded **down** to `lot_step` (MT5 `volume_step`, else `0.01`). `null` (shown as “not available”) when it cannot be computed. |
| `contract_size` / `point_value` / `lot_step` | From MT5 `symbol_info` when available, else sensible fallbacks (`contract_size = 100` for XAUUSD). |
| `reason_human` / `next_buy_condition` / `next_condition` / `notes` | Plain-English reason, the condition needed for the next BUY (`next_condition` is retained for NONE compatibility), and a “signal-only reference; no order is sent” note. |

**Why NONE has no entry price.** A `NONE` is a no-entry candle. The plan
deliberately leaves `reference_entry_price`, `initial_stop_price`,
`take_profit_price`, `risk_per_unit`, `risk_amount` and `suggested_lot` as `null`
so nothing can be mistaken for an order. It still carries `reason_human`
(*e.g. “No entry: SuperTrend regime is bearish on the latest closed H4 candle.
The strategy waits for a fresh bullish flip.”*) and `next_buy_condition`, which
explains that a fresh bullish flip on a fully closed H4 candle is required. The
current SuperTrend value is only a movable reference boundary.

`signals.csv` flattens the most useful fields: `signal_id`, `generated_at`,
`signal_time`, `symbol`, `timeframe`, `strategy_id`, `signal_type`, `reason`,
`reason_human`, `close_price`, `atr_value`, `strategy_regime`,
`buy_zone_level`, `distance_to_buy_zone_price`, `distance_to_buy_zone_atr`,
`distance_to_buy_zone_pct`, `buy_zone_relation`, `reference_entry_price`,
`initial_stop_price`, `trailing_stop_reference`, `take_profit_price`,
`risk_percent`, `suggested_lot`, `execution_enabled`.

### BUY vs NO ENTRY

- `BUY` means the latest fully closed strategy candle created a **fresh** long
  entry event: a bullish SuperTrend flip for D or a Donchian high breakout for C.
- `NONE` / **NO ENTRY** means there is no fresh long entry on that candle. For D,
  this can mean the regime is bearish and waiting for a flip, or that it is
  already bullish and the strategy refuses to repeat the same setup.
- v1.7.x is long-only. A bearish D regime means **no long setup**; it is not a
  sell signal. Bearish conditions explain why there is no long entry, but they
  do not create a SELL or SHORT signal.

### `recent_checks` vs the official signal history (v1.7.3)

- **`recent_checks.json`** (and `GET /recent-checks`) is a *diagnostics* view: it
  re-evaluates the **latest N closed candles** every check (default 10, max 100)
  so you can answer *“what happened over the last several candles?”*. It is
  recomputed each run and is **not** an emission log — it never creates a signal.
- **`signals.csv`** (and `GET /history`) is the *official* log: **exactly one row
  per closed candle**, written once, deduplicated by `signal_time`. Refreshing the
  recent-checks diagnostics never adds a second official signal for the same
  candle.

### Read them via the API (optional)

The FastAPI backend exposes read-only endpoints that read these files (the
bridge process is independent and does **not** need to run inside FastAPI):

- `GET /api/strategy-lab/signals/latest`
- `GET /api/strategy-lab/signals/history?limit=50`
- `GET /api/strategy-lab/signals/recent-checks?limit=20` *(v1.7.2)*

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

- `execution_enabled` is `false` in every record, every CSV row, every
  recent-check row, and every API response.
- The module-level lock `EXECUTION_ENABLED = False` and the runtime guard
  `assert_signal_only()` (called before every evaluation) enforce it.
- The enriched **`trading_plan`** is a labelled *reference* only: a `NONE` never
  carries an entry/stop/lot, and the `suggested_lot` is sizing guidance, not an
  order. Tests assert the BUY/NONE plan shape and that no order-execution tokens
  ever appear in a serialized record.
- A test (`test_no_order_execution_functions_referenced`) asserts that no
  order/trade-mutating MT5 call (`order_send`, `order_check`, `order_modify`,
  `order_close`, `position_close`, `TRADE_ACTION`, …) appears anywhere in the
  bridge package. There are **no** order-execution API endpoints.
- The recent-checks diagnostics re-run every check but **never** emit a second
  official signal for an already-processed candle (one signal per closed candle).

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
