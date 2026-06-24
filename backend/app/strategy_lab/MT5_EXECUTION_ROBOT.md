# Strategy Lab v1.8 — MT5 Demo Execution Robot

> **Demo only. Dry-run is the default. Live trading is disabled.**
> The robot sends orders **only** when the connected MT5 account is *detected as a
> demo account*, execution is explicitly enabled **and** confirmed, the config is
> the supported **D SuperTrend H4** strategy, and every safety gate passes. It is
> **long-only**: it opens a BUY on a fresh signal, trails the stop **upward only**,
> **never closes** a position, and **never** sends a SELL/SHORT order. It never
> handles a broker login/password and never stores credentials.

The execution robot is a **separate module, separate API and separate UI panel**
from the signal-only bridge. The signal-only bridge
(`mt5_signal_bridge.py` / `/api/strategy-lab/signals`) is left completely intact
and is **never** converted into a trading component — the robot only *reuses its
read-only research helpers* so the live entry stays byte-for-byte aligned with
the backtester.

---

## 1. Demo-only principle

Orders are sent only when **all** of these hold:

| Gate | Requirement |
| --- | --- |
| `execution_enabled` | `true` (dry-run otherwise) |
| `demo_only` | `true` |
| Account `trade_mode` | **detected demo** (`ACCOUNT_TRADE_MODE_DEMO`) |
| Config | supported **D** (`D_supertrend_h4_trailing_risk`) |
| Direction | `long_only` |
| `ml_filter_enabled` | `false` |
| Duplicate guard | no order yet for this `signal_time` |
| One-position-only | no existing BUY position for symbol/magic |
| Margin | `required_margin <= free_margin` |
| Lot sizing | valid (`>= volume_min`, rounded to `volume_step`) |

A **live or unknown** account is always refused. `live_execution` is **not
implemented** in v1.8 and is always refused — there is no "allow live trading"
switch anywhere in the code, API or UI.

## 2. Signal bridge vs execution robot

| | Signal-only bridge (v1.7) | Demo execution robot (v1.8) |
| --- | --- | --- |
| Module | `mt5_signal_bridge.py` | `mt5_execution_robot.py` |
| Manager | `mt5_bridge_manager.py` | `mt5_execution_manager.py` |
| Store | `signal_store.py` | `execution_store.py` |
| API | `/api/strategy-lab/signals` | `/api/strategy-lab/execution` |
| Output dir | `reports/mt5_signal_bridge/` | `reports/mt5_execution_robot/` |
| Sends orders? | **never** | only on a detected demo account |
| Strategies | D and C | **D only** |

The robot **reuses** the bridge for config loading/validation, MT5
initialize/shutdown, rates fetching, the closed-candle rule and the rule-based
signal record. It does **not** duplicate any indicator/strategy logic.

## 3. Dry-run vs demo execution

* **Dry-run** (default) — computes what *would* happen and writes a decision, but
  **never** sends an order. Safe on any account. Possible actions:
  `no_action`, `would_open_buy`, `would_update_trailing_sl`, `refused`.
* **Demo execution** — sends orders, but only after every gate passes. Possible
  actions: `no_action`, `opened_buy`, `updated_trailing_sl`, `refused`.

Only a **fully closed H4 candle** is ever used (the forming candle is dropped),
and a BUY is opened only on a **fresh** SuperTrend bullish flip — repeated
bullish candles never re-enter.

## 4. How to export / save a D config

1. In the Strategy Lab page, select preset **D · SuperTrend H4 — ATR trailing
   (risk %)** and tune parameters if desired.
2. Either click **Export config (JSON)** to download it, or open the
   **MT5 Demo Execution Robot** panel and click **Save current config for robot**
   (this saves it under `MetaTrader_Data/configs/`, shared with the Signal
   Bridge).
3. Only **D** configs are accepted. A C config is refused with:
   *"Execution robot v1.8 supports only D SuperTrend H4. C remains
   research/signal-only."*

## 5. How to use the UI panel

The **MT5 Demo Execution Robot** panel sits **below** the MT5 Signal Bridge
panel and is **collapsed by default** with a strong warning. Open it, then:

1. **Config** — pick a saved D config (shared with the Signal Bridge).
2. **Safety checklist** — badges for MT5 connected, account detected, demo
   account, strategy D supported, direction long-only, ML disabled, mode, and
   one-position-only. They refresh from the latest decision.
3. **Position sizing** — pick **Auto risk %** (default), **Manual lot**, or
   **Auto risk % with max lot**. Manual lot has quick-pick buttons (0.01–0.10), a
   max-manual-risk % field and an **Allow high manual risk** checkbox. Read-outs
   (calculated/resolved/final lot, implied/final risk) update after a dry-run.
4. **Dry-run** — click **Dry-run once** to see what the robot *would* do. Safe on
   any account; never sends an order.
5. **Demo execution controls** — tick **both** confirmations
   ("I understand this can place orders on the connected MT5 DEMO account" and
   "I confirm the connected account is a demo account"). The **Demo execution
   once** and **Start demo execution polling** buttons stay disabled until both
   are ticked, the sizing inputs are valid, and any high-manual-risk warning is
   acknowledged.
6. **Polling** — set the poll interval and use **Start dry-run polling** or
   **Start demo execution polling** / **Stop robot**.
7. **Latest decision** — shows mode, sizing mode, action, signal, entry/SL,
   final lot, implied/final risk amount and %, required/free margin, position,
   trailing diagnostics, sizing warnings and the order result.
8. **Execution history** — recent decisions (generated_at, mode, action,
   signal_time, signal type, symbol, lot, entry, initial SL, retcode, refusals).
9. **Logs** — collapsed by default; stdout/stderr tails of the polling process.

The wording is deliberately demo-only: **Dry-run**, **Would open BUY**,
**Opened BUY on demo**, **Trailing SL update**. There is no "trade now" / "go
live" / "send order" control.

## 6. CLI fallback

```bash
# Dry-run once
python backend/app/strategy_lab/run_mt5_execution_robot.py \
  --config MetaTrader_Data/configs/D_supertrend_h4.json --once

# Dry-run polling (Ctrl-C to stop)
python backend/app/strategy_lab/run_mt5_execution_robot.py \
  --config MetaTrader_Data/configs/D_supertrend_h4.json --poll-seconds 60

# Demo execution once (sends orders ONLY on a detected demo account)
python backend/app/strategy_lab/run_mt5_execution_robot.py \
  --config MetaTrader_Data/configs/D_supertrend_h4.json --once \
  --execution-enabled --confirm-demo-execution

# Demo execution polling
python backend/app/strategy_lab/run_mt5_execution_robot.py \
  --config MetaTrader_Data/configs/D_supertrend_h4.json --poll-seconds 60 \
  --execution-enabled --confirm-demo-execution
```

`--execution-enabled` without `--confirm-demo-execution` is downgraded to
dry-run with a warning. Other flags: `--symbol`, `--bars`, `--magic` (default
`170801`), `--deviation` (default `50`), `--allow-min-lot-rounding`,
`--output-dir`.

## 7. Where logs are stored

All under `MetaTrader_Data/reports/mt5_execution_robot/` (git-ignored):

| File | Contents |
| --- | --- |
| `latest_execution_decision.json` | the most recent full decision record |
| `execution_events.csv` | append-only log, one row per decision |
| `execution_state.json` | last open-attempt `signal_time` (duplicate guard) |
| `execution_process.json` | polling process state (pid, mode, config, …) |
| `robot_stdout.log` / `robot_stderr.log` | polling process output |

## 8. Safety gates (decision fields)

Every decision carries `mode` (`dry_run` / `demo_execution` / `refused`),
`intended_action`, `refusal_reasons`, `account` (incl. `is_demo`), `sizing`
diagnostics, `position_state`, `trailing` diagnostics and `order_result`.
Refusal reasons you may see:

* `account_is_not_demo_live_or_unknown` — not a detected demo account.
* `demo_execution_not_confirmed` — confirmation flag missing.
* `demo_only_flag_required` / `execution_not_enabled` — execution gates.
* `lot_below_minimum` — computed lot below `volume_min`
  (pass `allow_min_lot_rounding` to round up to `volume_min`, which **increases
  risk** and sets `increased_risk_due_to_min_lot`). A **manual** lot is never
  silently bumped — it is refused instead.
* `lot_above_maximum` — lot above `volume_max` (manual lot too large).
* `manual_lot_required` — `fixed_lot_manual` with no positive `manual_lot`.
* `manual_risk_too_high` — a manual lot's implied risk exceeds
  `max_manual_risk_percent` in demo execution and `allow_high_manual_risk` is
  off (in dry-run this is only a warning, not a refusal).
* `margin_insufficient` — `required_margin > free_margin`.
* `invalid_lot_sizing` — missing inputs (equity / ATR / price).
* `order_send_failed` — MT5 rejected the order (see `order_result.retcode`).

## 9. Order sizing (risk-percent)

```
risk_amount       = equity * risk_percent / 100
initial_stop_price = entry_price - initial_stop_loss_atr * atr
risk_per_unit     = entry_price - initial_stop_price
raw_lot           = risk_amount / (risk_per_unit * contract_size)
rounded_lot       = round_down(raw_lot, volume_step)   # capped at volume_max
```

`entry_price` is the current **ask**. `contract_size` prefers
`symbol_info.trade_contract_size`, then the config, then `100.0` (XAUUSD).
`required_margin` uses `mt5.order_calc_margin` when available.

## 9a. Position sizing modes (v1.9)

The robot supports three **position sizing modes** (long-only; the demo-only
safety gates above are unchanged). The mode is chosen in the UI **Position
sizing** block, the API request, or the CLI. `initial_stop_price`/`risk_per_unit`
are always computed from the ATR stop, so the implied risk of any lot is shown.

| Mode | Sizes from | Notes |
| --- | --- | --- |
| `risk_percent_auto` *(default)* | equity × risk % | unchanged v1.8 behaviour |
| `fixed_lot_manual` | your `manual_lot` | `risk_percent` is **not** used to size |
| `risk_percent_with_max_lot` | risk % then capped at `max_lot` | `min(auto_lot, max_lot)` |

**Manual lot (`fixed_lot_manual`).** `manual_lot` must be `> 0`. It is rounded
down to `volume_step` (warning `manual_lot_rounded_to_symbol_step`), refused if
below `volume_min` (`lot_below_minimum`) or above `volume_max`
(`lot_above_maximum`), and margin is still checked. The **implied risk** is
reported:

```
implied_risk_amount  = risk_per_unit * contract_size * rounded_lot
implied_risk_percent = implied_risk_amount / account_equity * 100
```

If `implied_risk_percent > max_manual_risk_percent` (default **3.0**):

* **dry-run** — allowed, but `sizing_status = warning_manual_risk_too_high`
  (warning `manual_risk_exceeds_max_manual_risk_percent`);
* **demo execution** — **refused** (`manual_risk_too_high`) **unless**
  `allow_high_manual_risk = true`.

> ⚠️ A manual lot can risk **more** than the strategy's configured risk %.
> **Use dry-run first** and check the implied risk before any demo execution.

**Max-lot cap (`risk_percent_with_max_lot`).** Sizes by risk %, then caps:
`final_lot = round_down(min(auto_lot, max_lot), volume_step)`. The decision
reports `auto_lot_before_cap`, `max_lot` and `capped_by_max_lot`.

**Extra `sizing` fields (v1.9):** `execution_sizing_mode`,
`manual_lot_requested`, `max_lot`, `auto_lot_before_cap`, `raw_lot`,
`rounded_lot`, `final_lot`, `capped_by_max_lot`, `implied_risk_amount`,
`implied_risk_percent`, `final_risk_amount`, `final_risk_percent`,
`max_manual_risk_percent`, `allow_high_manual_risk`, `sizing_warnings`.

**API/CLI fields (all default to the v1.8 behaviour):**
`execution_sizing_mode` (`risk_percent_auto`), `manual_lot` (`null`), `max_lot`
(`null`), `max_manual_risk_percent` (`3.0`), `allow_high_manual_risk` (`false`).
They are accepted by `POST /execution/dry-run-once`, `/demo-once` and `/start`.

```bash
# Dry-run with a manual 0.05 lot (safe; never sends)
python backend/app/strategy_lab/run_mt5_execution_robot.py \
  --config MetaTrader_Data/configs/D_supertrend_h4.json --once \
  --execution-sizing-mode fixed_lot_manual --manual-lot 0.05

# Demo execution with a manual 0.05 lot (detected demo account only)
python backend/app/strategy_lab/run_mt5_execution_robot.py \
  --config MetaTrader_Data/configs/D_supertrend_h4.json --once \
  --execution-enabled --confirm-demo-execution \
  --execution-sizing-mode fixed_lot_manual --manual-lot 0.05

# Auto risk %, capped at 0.10 lot
python backend/app/strategy_lab/run_mt5_execution_robot.py \
  --config MetaTrader_Data/configs/D_supertrend_h4.json --once \
  --execution-sizing-mode risk_percent_with_max_lot --max-lot 0.10

# A high-risk manual lot is refused in demo unless you allow it
python backend/app/strategy_lab/run_mt5_execution_robot.py \
  --config MetaTrader_Data/configs/D_supertrend_h4.json --once \
  --execution-enabled --confirm-demo-execution \
  --execution-sizing-mode fixed_lot_manual --manual-lot 0.50 \
  --max-manual-risk-percent 3.0 --allow-high-manual-risk
```

## 10. Trailing SL (upward only)

```
trailing_stop_candidate = latest_closed_close - trailing_stop_atr * atr
```

The SL is raised only if the candidate is **above** the current SL, **below**
the current bid, and respects the broker stop level (when exposed). The SL is
**never** moved downward and the position is **never** closed. If the setup ends
(a bearish flip) while a position is open, the decision records
`setup_ended_but_no_close_in_v1_8` and keeps the position.

## 11. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `pip install MetaTrader5` | The `MetaTrader5` package is missing in the backend venv. |
| `mt5.initialize() failed` | The MT5 terminal is not open / not logged in. Open it. |
| `account_is_not_demo_live_or_unknown` | The connected account is live/unknown. Execution is refused; switch to a demo account. |
| Symbol not found | Pass the broker's exact name (e.g. `--symbol XAUUSDrfd`). |
| `lot_below_minimum` | Risk/stop produce a sub-minimum lot. Increase risk %, or pass `allow_min_lot_rounding` (raises risk). |
| `margin_insufficient` | Free margin is below the required margin for the lot. |
| `duplicate_signal_time_already_processed` | A BUY was already attempted for this closed candle — one order per candle by design. |
| Action stuck on `no_action` with a position | One-position-only: the robot won't open a second position; it only trails the stop. |
