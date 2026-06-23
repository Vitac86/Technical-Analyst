import { useCallback, useEffect, useRef, useState } from "react";

import {
  checkMt5Readiness,
  checkSignalOnce,
  fetchExportedConfig,
  getRecentChecks,
  getSignalBridgeStatus,
  getSignalHistory,
  getSignalLogs,
  listSignalConfigs,
  saveSignalConfig,
  startSignalBridge,
  stopSignalBridge,
} from "../../api/strategyLab";
import type {
  BacktestRequest,
  BridgeProcessStatus,
  Mt5Readiness,
  RecentCheck,
  SavedSignalConfig,
  SignalLogsResponse,
  SignalRecord,
} from "../../types/strategyLab";
import { fmtDateTime } from "./format";

interface Props {
  /** Build the current Strategy Lab config request (preset + overrides + costs). */
  buildConfigBody: () => BacktestRequest;
  /** Disabled while a backtest is running on the page. */
  disabled?: boolean;
}

// A signal older than this is flagged as potentially stale in the UI.
const STALE_MS = 6 * 60 * 60 * 1000;

function fmtCell(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = typeof value === "string" ? Number(value) : value;
  if (Number.isFinite(num)) return num.toFixed(num >= 1000 ? 1 : 3);
  return String(value);
}

/** Suggested lot is a reference, never an order: show "not available" when null. */
function fmtLot(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "not available";
  const num = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(num)) return "not available";
  return num.toFixed(2);
}

/** NONE remains a concise status badge; reasons use human-readable wording. */
function signalLabel(signalType: string | null | undefined): string {
  return signalType === "BUY" ? "BUY" : "NO ENTRY";
}

function signalClass(signalType: string | null | undefined): string {
  return signalType === "BUY"
    ? "slb-signal slb-signal-buy"
    : "slb-signal slb-signal-none";
}

function regimeLabel(
  regime: string | null | undefined,
  isFreshLongSignal = false,
): string {
  if (!regime) return "—";
  if (regime === "bearish") return "Bearish / no long setup";
  if (regime === "bullish") {
    return isFreshLongSignal
      ? "Bullish / fresh BUY signal"
      : "Bullish / already in setup";
  }
  return regime.charAt(0).toUpperCase() + regime.slice(1);
}

function regimeClass(regime: string | null | undefined): string {
  if (regime === "bullish") return "slb-badge slb-badge-ok";
  if (regime === "bearish") return "slb-badge slb-badge-err";
  if (regime === "neutral") return "slb-badge slb-badge-warn";
  return "slb-badge slb-badge-idle";
}

function relationLabel(relation: string | null | undefined): string {
  if (relation === "below_buy_zone") return "Below buy zone";
  if (relation === "above_buy_zone") return "Above buy zone";
  if (relation === "at_buy_zone") return "At buy zone";
  return "Unknown";
}

function relationShortLabel(relation: string | null | undefined): string {
  if (relation === "below_buy_zone") return "below";
  if (relation === "above_buy_zone") return "above";
  if (relation === "at_buy_zone") return "at";
  return "unknown";
}

function relationClass(relation: string | null | undefined): string {
  if (relation === "below_buy_zone") return "slb-badge slb-badge-warn";
  if (relation === "above_buy_zone") return "slb-badge slb-badge-ok";
  if (relation === "at_buy_zone") return "slb-badge slb-badge-idle";
  return "slb-badge slb-badge-idle";
}

function humanReason(record: SignalRecord): string {
  if (record.reason_human) return record.reason_human;
  return record.reason === "no_entry"
    ? "No fresh entry signal"
    : record.reason;
}

const EQUITY_SOURCE_LABEL: Record<string, string> = {
  mt5_account_equity: "MT5 account equity",
  config_initial_equity: "config initial_equity (MT5 equity unavailable)",
  unavailable: "not available",
};

function readinessClass(status: Mt5Readiness["status"]): string {
  if (status === "ok") return "slb-badge slb-badge-ok";
  if (status === "warning") return "slb-badge slb-badge-warn";
  return "slb-badge slb-badge-err";
}

function isStale(record: SignalRecord | null): boolean {
  if (!record?.generated_at) return false;
  const t = new Date(record.generated_at).getTime();
  if (Number.isNaN(t)) return false;
  return Date.now() - t > STALE_MS;
}

export function MT5SignalBridgePanel({ buildConfigBody, disabled }: Props) {
  const [configs, setConfigs] = useState<SavedSignalConfig[]>([]);
  const [selectedPath, setSelectedPath] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);

  const [readiness, setReadiness] = useState<Mt5Readiness | null>(null);
  const [checkingMt5, setCheckingMt5] = useState(false);

  const [pollSeconds, setPollSeconds] = useState(60);
  const [status, setStatus] = useState<BridgeProcessStatus | null>(null);
  const [latest, setLatest] = useState<SignalRecord | null>(null);
  const [history, setHistory] = useState<SignalRecord[]>([]);
  const [recentChecks, setRecentChecks] = useState<RecentCheck[]>([]);

  const [logs, setLogs] = useState<SignalLogsResponse | null>(null);
  const [logsOpen, setLogsOpen] = useState(false);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const running = status?.running ?? false;
  const hasConfig = Boolean(selectedPath);
  const actionsDisabled = disabled || busy;

  const refreshConfigs = useCallback(async () => {
    const data = await listSignalConfigs();
    setConfigs(data.configs);
    setSelectedPath((prev) => {
      if (prev && data.configs.some((c) => c.path === prev)) return prev;
      return data.configs[0]?.path ?? "";
    });
  }, []);

  const refreshStatus = useCallback(async () => {
    const data = await getSignalBridgeStatus();
    setStatus(data);
    // The status carries the full *enriched* latest_signal.json record; prefer it
    // for the cards (CSV history rows are flat and lack the nested objects).
    if (data.latest_signal !== undefined) setLatest(data.latest_signal ?? null);
    if (data.recent_checks) setRecentChecks(data.recent_checks);
  }, []);

  const refreshHistory = useCallback(async () => {
    const data = await getSignalHistory(50);
    setHistory(data.signals);
  }, []);

  const refreshRecentChecks = useCallback(async () => {
    const data = await getRecentChecks(20);
    setRecentChecks(data.recent_checks);
  }, []);

  // Initial load.
  useEffect(() => {
    void refreshConfigs().catch(() => undefined);
    void refreshStatus().catch(() => undefined);
    void refreshHistory().catch(() => undefined);
    void refreshRecentChecks().catch(() => undefined);
  }, [refreshConfigs, refreshStatus, refreshHistory, refreshRecentChecks]);

  // Poll status while the bridge is running so the UI stays current.
  const runningRef = useRef(running);
  runningRef.current = running;
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      void refreshStatus().catch(() => undefined);
      void refreshHistory().catch(() => undefined);
    }, 15000);
    return () => window.clearInterval(id);
  }, [running, refreshStatus, refreshHistory]);

  const guard = useCallback(
    async (label: string, fn: () => Promise<void>) => {
      setBusy(true);
      setError(null);
      try {
        await fn();
      } catch (err) {
        setError(err instanceof Error ? err.message : `${label} failed.`);
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const handleSave = () =>
    guard("Save config", async () => {
      setSaving(true);
      setSavedNote(null);
      try {
        const config = await fetchExportedConfig(buildConfigBody());
        const saved = await saveSignalConfig(config);
        setSavedNote(`Saved ${saved.file_name}`);
        await refreshConfigs();
        setSelectedPath(saved.path);
      } finally {
        setSaving(false);
      }
    });

  const handleCheckMt5 = () =>
    guard("MT5 check", async () => {
      setReadiness(null);
      setCheckingMt5(true);
      try {
        const result = await checkMt5Readiness(selectedPath);
        setReadiness(result);
      } finally {
        setCheckingMt5(false);
      }
    });

  const handleCheckOnce = () =>
    guard("Check once", async () => {
      const result = await checkSignalOnce(selectedPath);
      if (!result.ok && result.stderr) setError(result.stderr);
      if (result.signal) setLatest(result.signal);
      if (result.recent_checks) setRecentChecks(result.recent_checks);
      await refreshHistory();
      await refreshStatus();
    });

  const handleStart = () =>
    guard("Start polling", async () => {
      const result = await startSignalBridge(selectedPath, pollSeconds);
      setStatus(result);
      if (result.message && result.started === false) setSavedNote(result.message);
    });

  const handleStop = () =>
    guard("Stop polling", async () => {
      const result = await stopSignalBridge();
      setStatus(result);
    });

  const handleRefreshLogs = () =>
    guard("Refresh logs", async () => {
      const data = await getSignalLogs(100);
      setLogs(data);
      setLogsOpen(true);
    });

  const mt5Hint =
    readiness && readiness.status === "error"
      ? "Install MetaTrader5 in the backend venv and open / log in to the MT5 terminal."
      : null;

  // Derived views of the latest enriched signal for the cards below.
  const snapshot = latest?.market_snapshot;
  const state = latest?.strategy_state;
  const plan = latest?.trading_plan;
  const isBuy = latest?.signal_type === "BUY";
  const isDonchian = (latest?.strategy_id ?? "").includes("donchian");
  const buyZoneLevel = state?.buy_zone_level ?? latest?.buy_zone_level;
  const distanceToBuyZonePrice =
    state?.distance_to_buy_zone_price ?? latest?.distance_to_buy_zone_price;
  const distanceToBuyZoneAtr =
    state?.distance_to_buy_zone_atr ?? latest?.distance_to_buy_zone_atr;
  const distanceToBuyZonePct =
    state?.distance_to_buy_zone_pct ?? latest?.distance_to_buy_zone_pct;
  const buyZoneRelation =
    state?.buy_zone_relation ?? latest?.buy_zone_relation;
  const nextBuyCondition =
    state?.next_buy_condition ??
    plan?.next_buy_condition ??
    plan?.next_condition ??
    latest?.next_buy_condition;
  const equitySourceLabel = plan?.account_equity_source
    ? (EQUITY_SOURCE_LABEL[plan.account_equity_source] ?? plan.account_equity_source)
    : null;

  return (
    <section className="panel slb-panel">
      <div className="panel-header">
        <h2>MT5 Signal Bridge</h2>
        <span className="slb-safety-badge">
          Signal-only mode. Execution disabled.
        </span>
      </div>

      <p className="slb-intro">
        Run the confirmed rule-based strategy against your local MetaTrader 5
        terminal in <strong>signal-only</strong> mode. It reads candles and emits
        alerts; it never sends, modifies or closes orders.
      </p>

      {error ? <div className="chart-state chart-state-error">{error}</div> : null}

      {/* A. Config for bridge */}
      <div className="slb-section">
        <h3 className="slb-section-title">Config</h3>
        <div className="slb-row">
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={actionsDisabled || saving}
          >
            {saving ? "Saving…" : "Save current config for bridge"}
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => guard("Refresh configs", refreshConfigs)}
            disabled={actionsDisabled}
          >
            Refresh configs
          </button>
          {savedNote ? <span className="slb-note">{savedNote}</span> : null}
        </div>

        <label className="sl-field slb-config-select">
          <span className="sl-field-label">Saved configs</span>
          <select
            className="sl-input"
            value={selectedPath}
            disabled={actionsDisabled || configs.length === 0}
            onChange={(e) => setSelectedPath(e.target.value)}
          >
            {configs.length === 0 ? (
              <option value="">No saved configs yet</option>
            ) : null}
            {configs.map((c) => (
              <option key={c.path} value={c.path}>
                {c.file_name} · {c.strategy_id ?? "?"} {c.timeframe ?? ""}
              </option>
            ))}
          </select>
        </label>
        {selectedPath ? (
          <p className="slb-path" title={selectedPath}>
            {selectedPath}
          </p>
        ) : (
          <p className="slb-hint">Save current config for bridge first.</p>
        )}
      </div>

      {/* B. MT5 readiness */}
      <div className="slb-section">
        <h3 className="slb-section-title">MT5 readiness</h3>
        <div className="slb-row">
          <button
            className="btn btn-secondary"
            onClick={handleCheckMt5}
            disabled={actionsDisabled || !hasConfig}
          >
            {checkingMt5 ? "Checking…" : "Check MT5 connection"}
          </button>
          {readiness ? (
            <span className={readinessClass(readiness.status)}>
              {readiness.status === "ok"
                ? "Ready"
                : readiness.status === "warning"
                  ? "Warning"
                  : "Error"}
            </span>
          ) : null}
        </div>
        {readiness ? (
          <div className="slb-readiness">
            <dl className="slb-kv">
              <div>
                <dt>Terminal connected</dt>
                <dd>{readiness.terminal_connected ? "yes" : "no"}</dd>
              </div>
              <div>
                <dt>Account connected</dt>
                <dd>{readiness.account_connected ? "yes" : "no"}</dd>
              </div>
              <div>
                <dt>Symbol</dt>
                <dd>{readiness.symbol ?? "—"}</dd>
              </div>
              <div>
                <dt>Timeframe</dt>
                <dd>{readiness.timeframe ?? "—"}</dd>
              </div>
              <div>
                <dt>Rates available</dt>
                <dd>
                  {readiness.rates_available
                    ? `yes (${readiness.bars_fetched} bars)`
                    : "no"}
                </dd>
              </div>
              <div>
                <dt>Latest closed candle</dt>
                <dd>{fmtDateTime(readiness.latest_closed_candle_time)}</dd>
              </div>
            </dl>
            {readiness.message ? (
              <p className="slb-message">{readiness.message}</p>
            ) : null}
            {mt5Hint ? <p className="slb-hint">{mt5Hint}</p> : null}
          </div>
        ) : null}
      </div>

      {/* C. Signal actions */}
      <div className="slb-section">
        <h3 className="slb-section-title">Signal actions</h3>
        {!hasConfig ? (
          <p className="slb-hint">Save current config for bridge first.</p>
        ) : null}
        <div className="slb-row">
          <button
            className="btn btn-secondary"
            onClick={handleCheckOnce}
            disabled={actionsDisabled || !hasConfig}
          >
            Check once
          </button>
          <label className="sl-field slb-poll">
            <span className="sl-field-label">Poll seconds</span>
            <input
              type="number"
              className="sl-input"
              min={5}
              max={86400}
              value={pollSeconds}
              disabled={actionsDisabled || running}
              onChange={(e) => setPollSeconds(Number(e.target.value) || 60)}
            />
          </label>
          <button
            className="btn btn-primary"
            onClick={handleStart}
            disabled={actionsDisabled || !hasConfig || running}
          >
            Start polling
          </button>
          <button
            className="btn btn-secondary"
            onClick={handleStop}
            disabled={actionsDisabled || !running}
          >
            Stop polling
          </button>
        </div>

        <div className="slb-status-line">
          <span
            className={
              running ? "slb-badge slb-badge-ok" : "slb-badge slb-badge-idle"
            }
          >
            {running ? "Running" : "Stopped"}
          </span>
          {status?.pid ? <span>pid {status.pid}</span> : null}
          {status?.started_at ? (
            <span>since {fmtDateTime(status.started_at)}</span>
          ) : null}
          {status?.poll_seconds ? (
            <span>every {status.poll_seconds}s</span>
          ) : null}
          {status?.config_path ? (
            <span className="slb-status-config" title={status.config_path}>
              {status.config_path.split(/[\\/]/).pop()}
            </span>
          ) : null}
        </div>
      </div>

      {/* D. Latest signal: status + market snapshot + trading plan cards */}
      <div className="slb-section">
        <h3 className="slb-section-title">Latest signal</h3>
        <div className="slb-safety-badge slb-safety-inline">
          Signal-only mode. Execution disabled. No order is ever sent.
        </div>
        {latest ? (
          <>
            {isStale(latest) ? (
              <div className="chart-state chart-state-warn">
                This signal may be stale (generated {fmtDateTime(latest.generated_at)}).
              </div>
            ) : null}

            <div className="slb-cards">
              {/* A. Signal status card */}
              <div className="slb-card">
                <div className="slb-card-head">
                  <span className="slb-card-title">Signal status</span>
                  <span
                    className={
                      isBuy
                        ? "slb-bigbadge slb-bigbadge-buy"
                        : "slb-bigbadge slb-bigbadge-none"
                    }
                  >
                    {signalLabel(latest.signal_type)}
                  </span>
                </div>
                <p className="slb-card-reason">
                  {humanReason(latest)}
                </p>
                <dl className="slb-kv slb-kv-wide">
                  <div>
                    <dt>Strategy regime</dt>
                    <dd>
                      <span className={regimeClass(state?.strategy_regime)}>
                        {regimeLabel(
                          state?.strategy_regime ?? latest.strategy_regime,
                          state?.is_new_long_signal ?? isBuy,
                        )}
                      </span>
                    </dd>
                  </div>
                  <div>
                    <dt>Fresh signal</dt>
                    <dd>{state?.is_new_long_signal ? "yes" : "no"}</dd>
                  </div>
                  <div>
                    <dt>Signal time</dt>
                    <dd>{fmtDateTime(latest.signal_time)}</dd>
                  </div>
                  <div>
                    <dt>Generated at</dt>
                    <dd>{fmtDateTime(latest.generated_at)}</dd>
                  </div>
                  <div>
                    <dt>Strategy</dt>
                    <dd>
                      {latest.symbol} {latest.timeframe} · {latest.strategy_id}
                    </dd>
                  </div>
                  <div>
                    <dt>Execution</dt>
                    <dd className="slb-exec-off">disabled</dd>
                  </div>
                </dl>
              </div>

              {/* B. Market snapshot card */}
              <div className="slb-card">
                <div className="slb-card-head">
                  <span className="slb-card-title">Market snapshot</span>
                </div>
                <dl className="slb-kv slb-kv-wide">
                  <div>
                    <dt>Close</dt>
                    <dd>{fmtCell(snapshot?.close_price ?? latest.close_price)}</dd>
                  </div>
                  <div>
                    <dt>ATR</dt>
                    <dd>{fmtCell(snapshot?.atr_value ?? latest.atr_value)}</dd>
                  </div>
                  <div>
                    <dt>Spread points</dt>
                    <dd>{fmtCell(snapshot?.spread_points)}</dd>
                  </div>
                  <div>
                    <dt>Latest closed candle</dt>
                    <dd>{fmtDateTime(snapshot?.latest_closed_candle_time)}</dd>
                  </div>
                  <div>
                    <dt>Previous closed candle</dt>
                    <dd>{fmtDateTime(snapshot?.previous_closed_candle_time)}</dd>
                  </div>
                  <div>
                    <dt>
                      {isDonchian
                        ? "Donchian breakout level"
                        : "SuperTrend reference boundary"}
                    </dt>
                    <dd>{fmtCell(buyZoneLevel)}</dd>
                  </div>
                  <div>
                    <dt>
                      {isDonchian
                        ? "Distance to breakout (price)"
                        : "Distance to bullish flip zone (price)"}
                    </dt>
                    <dd>{fmtCell(distanceToBuyZonePrice)}</dd>
                  </div>
                  <div>
                    <dt>
                      {isDonchian
                        ? "Distance to breakout (ATR)"
                        : "Distance to bullish flip zone (ATR)"}
                    </dt>
                    <dd>{fmtCell(distanceToBuyZoneAtr)}</dd>
                  </div>
                  <div>
                    <dt>
                      {isDonchian
                        ? "Distance to breakout (%)"
                        : "Distance to bullish flip zone (%)"}
                    </dt>
                    <dd>{fmtCell(distanceToBuyZonePct)}</dd>
                  </div>
                  <div>
                    <dt>Relation</dt>
                    <dd>
                      <span className={relationClass(buyZoneRelation)}>
                        {relationLabel(buyZoneRelation)}
                      </span>
                    </dd>
                  </div>
                  {isDonchian ? (
                    <>
                      <div>
                        <dt>Donchian high</dt>
                        <dd>{fmtCell(state?.donchian_high)}</dd>
                      </div>
                      <div>
                        <dt>Donchian low</dt>
                        <dd>{fmtCell(state?.donchian_low)}</dd>
                      </div>
                      <div>
                        <dt>Donchian position</dt>
                        <dd>{fmtCell(state?.donchian_position)}</dd>
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <dt>SuperTrend value</dt>
                        <dd>{fmtCell(state?.supertrend_value)}</dd>
                      </div>
                      <div>
                        <dt>SuperTrend distance (ATR)</dt>
                        <dd>{fmtCell(state?.supertrend_distance_atr)}</dd>
                      </div>
                    </>
                  )}
                </dl>
                {!isDonchian ? (
                  <p className="slb-reference-note">
                    The boundary is a reference from the latest closed candle.
                    SuperTrend can move on future candles.
                  </p>
                ) : null}
              </div>

              {/* C. Trading plan card */}
              <div className="slb-card">
                <div className="slb-card-head">
                  <span className="slb-card-title">Trading plan (reference only)</span>
                </div>
                {isBuy ? (
                  <>
                    <p className="slb-card-reason">{humanReason(latest)}</p>
                    <dl className="slb-kv slb-kv-wide">
                      <div>
                        <dt>Reference entry price</dt>
                        <dd>{fmtCell(plan?.reference_entry_price)}</dd>
                      </div>
                      <div>
                        <dt>Initial stop price</dt>
                        <dd>{fmtCell(plan?.initial_stop_price)}</dd>
                      </div>
                      <div>
                        <dt>Trailing stop reference</dt>
                        <dd>{fmtCell(plan?.trailing_stop_reference)}</dd>
                      </div>
                      <div>
                        <dt>Take profit</dt>
                        <dd>
                          {plan?.take_profit_price == null
                            ? "none"
                            : fmtCell(plan.take_profit_price)}
                        </dd>
                      </div>
                      <div>
                        <dt>Risk distance (per unit)</dt>
                        <dd>{fmtCell(plan?.risk_per_unit)}</dd>
                      </div>
                      <div>
                        <dt>Risk amount</dt>
                        <dd>{fmtCell(plan?.risk_amount)}</dd>
                      </div>
                      <div>
                        <dt>Suggested lot reference</dt>
                        <dd>{fmtLot(plan?.suggested_lot)}</dd>
                      </div>
                      <div>
                        <dt>Account equity reference</dt>
                        <dd>{fmtCell(plan?.account_equity_reference)}</dd>
                      </div>
                      {equitySourceLabel ? (
                        <div>
                          <dt>Equity source</dt>
                          <dd>{equitySourceLabel}</dd>
                        </div>
                      ) : null}
                    </dl>
                    <p className="slb-plan-note">
                      This is signal-only; no order is sent. “Suggested lot
                      reference” is sizing guidance, not an order instruction.
                    </p>
                  </>
                ) : (
                  <>
                    <div className="slb-next-buy">
                      <h4>Next BUY condition</h4>
                      <p className="slb-card-reason">{humanReason(latest)}</p>
                      {nextBuyCondition ? (
                        <p className="slb-next-buy-condition">
                          {nextBuyCondition}
                        </p>
                      ) : null}
                    </div>
                    <dl className="slb-kv slb-kv-wide">
                      <div>
                        <dt>
                          {isDonchian
                            ? "Donchian breakout level"
                            : "SuperTrend reference boundary"}
                        </dt>
                        <dd>{fmtCell(buyZoneLevel)}</dd>
                      </div>
                      <div>
                        <dt>Distance (price / ATR / %)</dt>
                        <dd>
                          {fmtCell(distanceToBuyZonePrice)} /{" "}
                          {fmtCell(distanceToBuyZoneAtr)} ATR /{" "}
                          {fmtCell(distanceToBuyZonePct)}%
                        </dd>
                      </div>
                      <div>
                        <dt>Relation</dt>
                        <dd>{relationLabel(buyZoneRelation)}</dd>
                      </div>
                      {plan?.trailing_stop_reference != null ? (
                        <div>
                          <dt>Trailing stop reference (informational)</dt>
                          <dd>{fmtCell(plan.trailing_stop_reference)}</dd>
                        </div>
                      ) : null}
                    </dl>
                    <p className="slb-plan-note">
                      This is signal-only; no order is sent. No entry price is
                      shown because there is no fresh entry signal.
                    </p>
                  </>
                )}
              </div>
            </div>
          </>
        ) : (
          <p className="slb-hint">No signal yet. Run a check or start polling.</p>
        )}
      </div>

      {/* D. Recent checks: what happened over the last several candles */}
      <div className="slb-section">
        <h3 className="slb-section-title">Recent checks (last closed candles)</h3>
        {recentChecks.length === 0 ? (
          <p className="slb-hint">
            No recent checks yet. Run <strong>Check once</strong> or start polling
            to see diagnostics over the last several closed candles.
          </p>
        ) : (
          <div className="slb-table-wrap">
            <table className="slb-table">
              <thead>
                <tr>
                  <th>Signal time</th>
                  <th>Close</th>
                  <th>ATR</th>
                  <th>Regime</th>
                  <th>Signal</th>
                  <th>Buy zone</th>
                  <th>Distance</th>
                  <th>Relation</th>
                  <th>Reason human</th>
                </tr>
              </thead>
              <tbody>
                {recentChecks.map((row) => (
                  <tr key={row.signal_time}>
                    <td>{fmtDateTime(row.signal_time)}</td>
                    <td>{fmtCell(row.close_price)}</td>
                    <td>{fmtCell(row.atr_value)}</td>
                    <td>
                      <span className={regimeClass(row.strategy_regime)}>
                        {regimeLabel(row.strategy_regime, row.is_long_signal)}
                      </span>
                    </td>
                    <td>
                      <span className={signalClass(row.signal_type)}>
                        {signalLabel(row.signal_type)}
                      </span>
                    </td>
                    <td>{fmtCell(row.buy_zone_level)}</td>
                    <td>
                      {fmtCell(row.distance_to_buy_zone_price)} /{" "}
                      {fmtCell(row.distance_to_buy_zone_atr)} ATR
                    </td>
                    <td>
                      <span className={relationClass(row.buy_zone_relation)}>
                        {relationShortLabel(row.buy_zone_relation)}
                      </span>
                    </td>
                    <td className="slb-table-reason">{row.reason_human}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* E. Signal history (official emitted signals) */}
      <div className="slb-section">
        <h3 className="slb-section-title">Signal history</h3>
        {history.length === 0 ? (
          <p className="slb-hint">No signals recorded yet.</p>
        ) : (
          <div className="slb-table-wrap">
            <table className="slb-table">
              <thead>
                <tr>
                  <th>Generated at</th>
                  <th>Signal time</th>
                  <th>Signal</th>
                  <th>Reason human</th>
                  <th>Close</th>
                  <th>Entry ref</th>
                  <th>Initial stop</th>
                  <th>Suggested lot</th>
                  <th>Exec</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.signal_id}>
                    <td>{fmtDateTime(row.generated_at)}</td>
                    <td>{fmtDateTime(row.signal_time)}</td>
                    <td>
                      <span className={signalClass(row.signal_type)}>
                        {signalLabel(row.signal_type)}
                      </span>
                    </td>
                    <td className="slb-table-reason">{humanReason(row)}</td>
                    <td>{fmtCell(row.close_price)}</td>
                    <td>{fmtCell(row.reference_entry_price)}</td>
                    <td>{fmtCell(row.initial_stop_price)}</td>
                    <td>{fmtLot(row.suggested_lot)}</td>
                    <td className="slb-exec-off">{String(row.execution_enabled)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* F. Logs */}
      <div className="slb-section">
        <div className="slb-row">
          <button
            className="btn btn-secondary"
            onClick={handleRefreshLogs}
            disabled={actionsDisabled}
          >
            Refresh logs
          </button>
          {logs ? (
            <button
              className="btn btn-ghost"
              onClick={() => setLogsOpen((v) => !v)}
            >
              {logsOpen ? "Hide logs" : "Show logs"}
            </button>
          ) : null}
        </div>
        {logs && logsOpen ? (
          <div className="slb-logs">
            <h4>stdout</h4>
            <pre className="slb-log-pre">{logs.stdout_tail || "(empty)"}</pre>
            <h4>stderr</h4>
            <pre className="slb-log-pre">{logs.stderr_tail || "(empty)"}</pre>
          </div>
        ) : null}
      </div>
    </section>
  );
}
