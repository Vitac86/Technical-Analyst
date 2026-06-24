import { useCallback, useEffect, useState } from "react";

import {
  checkMt5Readiness,
  checkSignalOnce,
  executionDemoOnce,
  executionDryRunOnce,
  fetchExportedConfig,
  getExecutionHistory,
  getExecutionStatus,
  getLatestExecutionDecision,
  getLatestSignal,
  getSignalBridgeStatus,
  listExecutionConfigs,
  saveSignalConfig,
  startExecutionRobot,
  stopExecutionRobot,
  stopSignalBridge,
} from "../../api/strategyLab";
import type {
  BacktestRequest,
  BridgeProcessStatus,
  ExecutionDecision,
  ExecutionHistoryRow,
  ExecutionSavedConfig,
  ExecutionStatus,
  Mt5Readiness,
  RecentCheck,
  SignalRecord,
} from "../../types/strategyLab";
import { fmtDateTime } from "./format";
import {
  resolveBoundaryDistance,
  resolveReferenceBoundary,
} from "./mt5SignalDiagnostics";
import { PositionSizingControls } from "./PositionSizingControls";
import { decisionFlagsHighManualRisk, usePositionSizing } from "./usePositionSizing";

interface Props {
  /** Build the current Strategy Lab config request (preset + overrides + costs). */
  buildConfigBody: () => BacktestRequest;
  /** Disabled while a backtest is running on the page. */
  disabled?: boolean;
}

const AUTO_REFRESH_MS = 15000;

/** Demo-only action wording (never "trade now" / "go live"). */
const ACTION_LABEL: Record<string, string> = {
  no_action: "No action",
  would_open_buy: "Would open BUY",
  opened_buy: "Opened BUY on demo",
  would_update_trailing_sl: "Would update trailing SL",
  updated_trailing_sl: "Updated trailing SL",
  refused: "Refused",
};

const MODE_LABEL: Record<string, string> = {
  dry_run: "Dry-run",
  demo_execution: "Demo execution",
  refused: "Refused",
};

const SIZING_MODE_LABEL: Record<string, string> = {
  risk_percent_auto: "Auto risk %",
  fixed_lot_manual: "Manual lot",
  risk_percent_with_max_lot: "Auto risk % + max lot",
};

const SIZING_WARNING_LABEL: Record<string, string> = {
  manual_lot_rounded_to_symbol_step: "Manual lot rounded to symbol step.",
  manual_risk_exceeds_max_manual_risk_percent:
    "Manual lot implies more than the risk ceiling.",
  lot_capped_by_max_lot: "Auto lot capped by max lot.",
};

const REFUSAL_LABEL: Record<string, string> = {
  account_is_not_demo_live_or_unknown:
    "Connected account is not a detected demo account (live or unknown).",
  execution_not_enabled: "Execution is not enabled.",
  demo_only_flag_required: "Demo-only safety flag is required.",
  demo_execution_not_confirmed: "Demo execution was not confirmed.",
  lot_below_minimum: "Computed lot is below the symbol minimum.",
  lot_above_maximum: "Lot is above the symbol maximum.",
  manual_lot_required: "A positive manual lot is required for fixed_lot_manual.",
  manual_risk_too_high:
    "Manual lot implies more risk than the ceiling; allow high manual risk to proceed.",
  margin_insufficient: "Free margin is insufficient for the computed lot.",
  invalid_lot_sizing: "Lot sizing could not be computed (missing inputs).",
  order_send_failed: "MT5 rejected the order (see order result).",
  live_execution_not_supported_in_v1_8: "Live execution is not implemented in v1.8.",
};

function fmtCell(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = typeof value === "string" ? Number(value) : value;
  if (Number.isFinite(num)) return num.toFixed(num >= 1000 ? 1 : 3);
  return String(value);
}

function fmtLot(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(num)) return "—";
  return num.toFixed(2);
}

function signalLabel(signalType: string | null | undefined): string {
  return signalType === "BUY" ? "BUY" : "NO ENTRY";
}

function regimeLabel(regime: string | null | undefined): string {
  if (!regime) return "—";
  if (regime === "bearish") return "Bearish";
  if (regime === "bullish") return "Bullish";
  return regime.charAt(0).toUpperCase() + regime.slice(1);
}

function regimeClass(regime: string | null | undefined): string {
  if (regime === "bullish") return "slb-badge slb-badge-ok";
  if (regime === "bearish") return "slb-badge slb-badge-err";
  if (regime === "neutral") return "slb-badge slb-badge-warn";
  return "slb-badge slb-badge-idle";
}

function readinessLabel(status: Mt5Readiness["status"] | null): string {
  if (status === "ok") return "Ready";
  if (status === "warning") return "Warning";
  if (status === "error") return "Error";
  return "Not checked";
}

function readinessTone(status: Mt5Readiness["status"] | null): string {
  if (status === "ok") return "tm-status-ok";
  if (status === "warning") return "tm-status-warn";
  if (status === "error") return "tm-status-err";
  return "tm-status-idle";
}

function humanReason(record: SignalRecord | null): string {
  if (!record) return "—";
  if (record.reason_human) return record.reason_human;
  return record.reason === "no_entry" ? "No fresh entry signal" : record.reason;
}

function robotStatusLabel(status: ExecutionStatus | null): string {
  if (!status?.running) return "Stopped";
  if (status.mode === "demo_execution") return "Demo execution polling";
  return "Dry-run polling";
}

export function TradingMonitorPanel({ buildConfigBody, disabled }: Props) {
  const [configs, setConfigs] = useState<ExecutionSavedConfig[]>([]);
  const [selectedPath, setSelectedPath] = useState<string>("");

  const [readiness, setReadiness] = useState<Mt5Readiness | null>(null);
  const [signalStatus, setSignalStatus] = useState<BridgeProcessStatus | null>(null);
  const [execStatus, setExecStatus] = useState<ExecutionStatus | null>(null);
  const [latestSignal, setLatestSignal] = useState<SignalRecord | null>(null);
  const [latestDecision, setLatestDecision] = useState<ExecutionDecision | null>(null);
  const [recentChecks, setRecentChecks] = useState<RecentCheck[]>([]);
  const [execHistory, setExecHistory] = useState<ExecutionHistoryRow[]>([]);

  const [pollSeconds, setPollSeconds] = useState(60);
  const [allowMinLot, setAllowMinLot] = useState(false);
  const [ackOrders, setAckOrders] = useState(false);
  const [ackDemo, setAckDemo] = useState(false);

  // v1.9 position sizing (mode + manual lot / max lot / manual-risk controls).
  const sizing = usePositionSizing();

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const running = (signalStatus?.running ?? false) || (execStatus?.running ?? false);
  const selected = configs.find((c) => c.path === selectedPath) ?? null;
  const supported = selected?.is_supported ?? false;
  const hasConfig = Boolean(selectedPath);
  const execReady = hasConfig && supported;
  const mt5Ok = readiness?.status === "ok";
  const demoConfirmed = ackOrders && ackDemo;
  const actionsDisabled = disabled || busy;
  // Dry-run needs a syntactically valid sizing input (e.g. a positive manual lot).
  const dryRunReady = execReady && sizing.valid && !actionsDisabled;
  // Demo execution is blocked while a high implied manual risk is unacknowledged.
  const demoBlockedByRisk =
    decisionFlagsHighManualRisk(latestDecision?.sizing) && !sizing.allowHighManualRisk;
  // Demo execution is gated: saved + supported config, MT5 ready, both confirmed,
  // valid sizing, and no unacknowledged high-manual-risk warning.
  const demoReady =
    execReady &&
    mt5Ok &&
    demoConfirmed &&
    sizing.valid &&
    !demoBlockedByRisk &&
    !actionsDisabled;

  const refreshConfigs = useCallback(async () => {
    const data = await listExecutionConfigs();
    setConfigs(data.configs);
    setSelectedPath((prev) => {
      if (prev && data.configs.some((c) => c.path === prev)) return prev;
      const firstSupported = data.configs.find((c) => c.is_supported);
      return firstSupported?.path ?? data.configs[0]?.path ?? "";
    });
  }, []);

  const refreshAll = useCallback(async () => {
    const [sStatus, eStatus, sLatest, eLatest, eHistory] = await Promise.all([
      getSignalBridgeStatus(),
      getExecutionStatus(),
      getLatestSignal(),
      getLatestExecutionDecision(),
      getExecutionHistory(5),
    ]);
    setSignalStatus(sStatus);
    setExecStatus(eStatus);
    setLatestSignal(sLatest.signal ?? sStatus.latest_signal ?? null);
    setLatestDecision(
      eLatest.latest_execution_decision ??
        eStatus.latest_execution_decision ??
        null,
    );
    if (sStatus.recent_checks) setRecentChecks(sStatus.recent_checks.slice(0, 5));
    setExecHistory(eHistory.events.slice(0, 5));
    setLastUpdated(new Date());
  }, []);

  // Initial load.
  useEffect(() => {
    void refreshConfigs().catch(() => undefined);
    void refreshAll().catch(() => undefined);
  }, [refreshConfigs, refreshAll]);

  // Auto-refresh while either the signal bridge or the execution robot runs.
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      void refreshAll().catch(() => undefined);
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [running, refreshAll]);

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

  const handleRefresh = () => guard("Refresh", refreshAll);

  const handleSave = () =>
    guard("Save config", async () => {
      setSavedNote(null);
      const config = await fetchExportedConfig(buildConfigBody());
      const saved = await saveSignalConfig(config);
      setSavedNote(`Saved ${saved.file_name}`);
      await refreshConfigs();
      setSelectedPath(saved.path);
    });

  const handleCheckMt5 = () =>
    guard("MT5 check", async () => {
      const result = await checkMt5Readiness(selectedPath);
      setReadiness(result);
    });

  const handleCheckOnce = () =>
    guard("Check signal once", async () => {
      const result = await checkSignalOnce(selectedPath);
      if (!result.ok && result.stderr) setError(result.stderr);
      if (result.signal) setLatestSignal(result.signal);
      if (result.recent_checks) setRecentChecks(result.recent_checks.slice(0, 5));
      await refreshAll();
    });

  const handleDryRun = () =>
    guard("Dry-run", async () => {
      const result = await executionDryRunOnce(selectedPath, {
        allowMinLotRounding: allowMinLot,
        ...sizing.options,
      });
      setLatestDecision(result);
      await refreshAll();
    });

  const handleDemoOnce = () =>
    guard("Demo execution", async () => {
      const result = await executionDemoOnce(selectedPath, demoConfirmed, {
        allowMinLotRounding: allowMinLot,
        ...sizing.options,
      });
      setLatestDecision(result);
      await refreshAll();
    });

  const handleStartDryRun = () =>
    guard("Start dry-run polling", async () => {
      const result = await startExecutionRobot({
        configPath: selectedPath,
        pollSeconds,
        allowMinLotRounding: allowMinLot,
        ...sizing.options,
      });
      setExecStatus(result);
      if (result.message && result.started === false) setSavedNote(result.message);
    });

  const handleStartDemo = () =>
    guard("Start demo execution polling", async () => {
      const result = await startExecutionRobot({
        configPath: selectedPath,
        pollSeconds,
        demoExecutionEnabled: true,
        confirmDemoExecution: demoConfirmed,
        allowMinLotRounding: allowMinLot,
        ...sizing.options,
      });
      setExecStatus(result);
      if (result.message && result.started === false) setSavedNote(result.message);
    });

  const handleStop = () =>
    guard("Stop", async () => {
      if (execStatus?.running) setExecStatus(await stopExecutionRobot());
      if (signalStatus?.running) setSignalStatus(await stopSignalBridge());
      await refreshAll();
    });

  // Derived views for the cards.
  const snapshot = latestSignal?.market_snapshot;
  const state = latestSignal?.strategy_state;
  const plan = latestSignal?.trading_plan;
  const isBuy = latestSignal?.signal_type === "BUY";
  const isDonchian = (latestSignal?.strategy_id ?? "").includes("donchian");
  const boundaryDiagnostics = {
    buy_zone_level: state?.buy_zone_level ?? latestSignal?.buy_zone_level,
    supertrend_value: state?.supertrend_value ?? latestSignal?.supertrend_value,
    donchian_high: state?.donchian_high,
    close_price: snapshot?.close_price ?? latestSignal?.close_price,
    atr_value: snapshot?.atr_value ?? latestSignal?.atr_value,
    distance_to_buy_zone_price:
      state?.distance_to_buy_zone_price ?? latestSignal?.distance_to_buy_zone_price,
    distance_to_buy_zone_atr:
      state?.distance_to_buy_zone_atr ?? latestSignal?.distance_to_buy_zone_atr,
    distance_to_buy_zone_pct:
      state?.distance_to_buy_zone_pct ?? latestSignal?.distance_to_buy_zone_pct,
    buy_zone_relation:
      state?.buy_zone_relation ?? latestSignal?.buy_zone_relation,
  };
  const boundary = resolveReferenceBoundary(boundaryDiagnostics, isDonchian);
  const distance = resolveBoundaryDistance(boundaryDiagnostics, isDonchian);
  const nextBuyCondition =
    state?.next_buy_condition ??
    plan?.next_buy_condition ??
    plan?.next_condition ??
    latestSignal?.next_buy_condition;

  const decisionSizing = latestDecision?.sizing;
  const trailing = latestDecision?.trailing;
  const position = latestDecision?.position_state;
  const market = latestDecision?.market;
  const executionMode =
    latestDecision?.mode ?? execStatus?.mode ?? "dry_run";

  return (
    <div className="sl-tab-panel tm-panel">
      {error ? <div className="chart-state chart-state-error">{error}</div> : null}

      {/* Refresh / status strip */}
      <div className="tm-toolbar">
        <div className="tm-toolbar-badges">
          <span className="slb-safety-badge">Live trading disabled</span>
          <span className="tm-note-badge">No SELL/SHORT in v1.8</span>
        </div>
        <div className="tm-toolbar-refresh">
          {lastUpdated ? (
            <span className="tm-updated">
              Updated {lastUpdated.toLocaleTimeString()}
              {running ? " · auto every 15s" : ""}
            </span>
          ) : null}
          <button
            className="btn btn-secondary"
            onClick={handleRefresh}
            disabled={actionsDisabled}
          >
            Refresh
          </button>
        </div>
      </div>

      {/* A. Top status row */}
      <div className="tm-status-row">
        <div className={`tm-status-card ${readinessTone(readiness?.status ?? null)}`}>
          <span className="tm-status-label">MT5 readiness</span>
          <span className="tm-status-value">{readinessLabel(readiness?.status ?? null)}</span>
        </div>
        <div className={`tm-status-card ${isBuy ? "tm-status-ok" : "tm-status-idle"}`}>
          <span className="tm-status-label">Signal</span>
          <span className="tm-status-value">{signalLabel(latestSignal?.signal_type)}</span>
        </div>
        <div className={`tm-status-card ${running ? "tm-status-ok" : "tm-status-idle"}`}>
          <span className="tm-status-label">Robot</span>
          <span className="tm-status-value">{robotStatusLabel(execStatus)}</span>
        </div>
        <div className="tm-status-card tm-status-idle">
          <span className="tm-status-label">Latest action</span>
          <span className="tm-status-value">
            {latestDecision
              ? ACTION_LABEL[latestDecision.intended_action] ??
                latestDecision.intended_action
              : "No action"}
          </span>
        </div>
      </div>

      {/* Main two-column area */}
      <div className="tm-grid">
        {/* B. Current trading state */}
        <section className="tm-card">
          <h3 className="tm-card-title">Current trading state</h3>
          {latestSignal ? (
            <dl className="slb-kv slb-kv-wide">
              <div>
                <dt>Symbol</dt>
                <dd>{latestSignal.symbol}</dd>
              </div>
              <div>
                <dt>Timeframe</dt>
                <dd>{latestSignal.timeframe}</dd>
              </div>
              <div>
                <dt>Strategy</dt>
                <dd>{latestSignal.strategy_id}</dd>
              </div>
              <div>
                <dt>Latest closed candle</dt>
                <dd>
                  {fmtDateTime(
                    snapshot?.latest_closed_candle_time ??
                      readiness?.latest_closed_candle_time,
                  )}
                </dd>
              </div>
              <div>
                <dt>Current regime</dt>
                <dd>
                  <span className={regimeClass(state?.strategy_regime ?? latestSignal.strategy_regime)}>
                    {regimeLabel(state?.strategy_regime ?? latestSignal.strategy_regime)}
                  </span>
                </dd>
              </div>
              <div>
                <dt>Signal type</dt>
                <dd>
                  <span
                    className={
                      isBuy
                        ? "slb-signal slb-signal-buy"
                        : "slb-signal slb-signal-none"
                    }
                  >
                    {signalLabel(latestSignal.signal_type)}
                  </span>
                </dd>
              </div>
              <div className="tm-kv-wide">
                <dt>Reason</dt>
                <dd>{humanReason(latestSignal)}</dd>
              </div>
              <div className="tm-kv-wide">
                <dt>Next BUY condition</dt>
                <dd>{nextBuyCondition ?? "—"}</dd>
              </div>
              <div>
                <dt>{isDonchian ? "Donchian breakout" : "SuperTrend boundary"}</dt>
                <dd>{fmtCell(boundary)}</dd>
              </div>
              <div>
                <dt>Distance to bullish flip</dt>
                <dd>
                  {fmtCell(distance.price)} / {fmtCell(distance.atr)} ATR
                </dd>
              </div>
              <div>
                <dt>Execution mode</dt>
                <dd>{MODE_LABEL[executionMode] ?? executionMode}</dd>
              </div>
              <div>
                <dt>Execution</dt>
                <dd>
                  <span className="slb-exec-off">disabled</span> · demo only
                </dd>
              </div>
            </dl>
          ) : (
            <p className="slb-hint">
              No signal yet. Use <strong>Check signal once</strong> below.
            </p>
          )}
        </section>

        {/* C. Trading plan / execution decision */}
        <section className="tm-card">
          <h3 className="tm-card-title">Trading plan / execution decision</h3>
          {latestDecision ? (
            <>
              <dl className="slb-kv slb-kv-wide">
                <div>
                  <dt>Sizing mode</dt>
                  <dd>
                    {SIZING_MODE_LABEL[
                      decisionSizing?.execution_sizing_mode ?? ""
                    ] ?? decisionSizing?.execution_sizing_mode ?? "—"}
                  </dd>
                </div>
                <div>
                  <dt>Entry reference (ask)</dt>
                  <dd>{fmtCell(decisionSizing?.entry_price ?? market?.ask)}</dd>
                </div>
                <div>
                  <dt>Initial SL</dt>
                  <dd>{fmtCell(decisionSizing?.initial_stop_price)}</dd>
                </div>
                <div>
                  <dt>Trailing SL candidate</dt>
                  <dd>{fmtCell(trailing?.trailing_stop_candidate)}</dd>
                </div>
                <div>
                  <dt>Final lot</dt>
                  <dd>
                    {fmtLot(decisionSizing?.final_lot ?? decisionSizing?.rounded_lot)}
                    {decisionSizing?.increased_risk_due_to_min_lot
                      ? " (min-lot ↑risk)"
                      : ""}
                    {decisionSizing?.capped_by_max_lot ? " (capped)" : ""}
                  </dd>
                </div>
                <div>
                  <dt>Risk amount / %</dt>
                  <dd>
                    {fmtCell(
                      decisionSizing?.implied_risk_amount ??
                        decisionSizing?.final_risk_amount ??
                        decisionSizing?.risk_amount,
                    )}
                    {(() => {
                      const pct =
                        decisionSizing?.implied_risk_percent ??
                        decisionSizing?.final_risk_percent;
                      return pct == null ? "" : ` · ${pct.toFixed(2)}%`;
                    })()}
                  </dd>
                </div>
                <div>
                  <dt>Required margin</dt>
                  <dd>{fmtCell(decisionSizing?.required_margin)}</dd>
                </div>
                <div>
                  <dt>Free margin</dt>
                  <dd>{fmtCell(decisionSizing?.free_margin)}</dd>
                </div>
                <div>
                  <dt>Position</dt>
                  <dd>
                    {position?.has_position
                      ? `BUY ${fmtLot(position.volume)} @ ${fmtCell(position.price_open)}`
                      : "none"}
                  </dd>
                </div>
              </dl>
              {(decisionSizing?.sizing_warnings ?? []).length > 0 ? (
                <ul className="erp-refusals erp-sizing-warnings tm-refusals">
                  {(decisionSizing?.sizing_warnings ?? []).map((w) => (
                    <li key={w}>{SIZING_WARNING_LABEL[w] ?? w}</li>
                  ))}
                </ul>
              ) : null}
              {latestDecision.refusal_reasons.length > 0 ? (
                <ul className="erp-refusals tm-refusals">
                  {latestDecision.refusal_reasons.map((r) => (
                    <li key={r}>{REFUSAL_LABEL[r] ?? r}</li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : plan && isBuy ? (
            <>
              <dl className="slb-kv slb-kv-wide">
                <div>
                  <dt>Reference entry</dt>
                  <dd>{fmtCell(plan.reference_entry_price)}</dd>
                </div>
                <div>
                  <dt>Initial SL</dt>
                  <dd>{fmtCell(plan.initial_stop_price)}</dd>
                </div>
                <div>
                  <dt>Trailing SL reference</dt>
                  <dd>{fmtCell(plan.trailing_stop_reference)}</dd>
                </div>
                <div>
                  <dt>Suggested lot</dt>
                  <dd>{fmtLot(plan.suggested_lot)}</dd>
                </div>
                <div>
                  <dt>Risk amount</dt>
                  <dd>{fmtCell(plan.risk_amount)}</dd>
                </div>
              </dl>
              <p className="slb-hint">
                Reference only. Run <strong>Dry-run once</strong> for ask, margins
                and rounded lot.
              </p>
            </>
          ) : (
            <p className="slb-hint">
              No execution decision yet. Run <strong>Dry-run once</strong> to see
              what the robot would do.
            </p>
          )}
        </section>
      </div>

      {/* D. Controls */}
      <section className="tm-card tm-controls-card">
        <h3 className="tm-card-title">Controls</h3>

        <div className="tm-config-row">
          <label className="sl-field tm-config-select">
            <span className="sl-field-label">Saved config (shared with bridge &amp; robot)</span>
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
                  {c.is_supported ? "" : " (unsupported)"}
                </option>
              ))}
            </select>
          </label>
          {selected && !supported ? (
            <span className="erp-badge erp-badge-err">
              Demo execution supports only D SuperTrend H4.
            </span>
          ) : null}
          {savedNote ? <span className="slb-note">{savedNote}</span> : null}
        </div>

        {/* Position sizing (v1.9) */}
        <div className="tm-control-group">
          <PositionSizingControls
            sizing={sizing}
            configRiskPercent={latestDecision?.sizing?.risk_percent ?? null}
            decisionSizing={latestDecision?.sizing ?? null}
            disabled={actionsDisabled}
          />
        </div>

        {/* Safe (signal-only / dry-run) controls */}
        <div className="tm-control-group">
          <span className="tm-control-group-label">Setup &amp; dry-run</span>
          <div className="slb-row">
            <button
              className="btn btn-primary"
              onClick={handleSave}
              disabled={actionsDisabled}
            >
              Save current config
            </button>
            <button
              className="btn btn-secondary"
              onClick={handleCheckMt5}
              disabled={actionsDisabled || !hasConfig}
            >
              Check MT5
            </button>
            <button
              className="btn btn-secondary"
              onClick={handleCheckOnce}
              disabled={actionsDisabled || !hasConfig}
            >
              Check signal once
            </button>
            <button
              className="btn btn-secondary"
              onClick={handleDryRun}
              disabled={!dryRunReady}
            >
              Dry-run once
            </button>
            <label className="sl-field slb-poll">
              <span className="sl-field-label">Poll s</span>
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
              onClick={handleStartDryRun}
              disabled={!dryRunReady || running}
            >
              Start dry-run polling
            </button>
            <button
              className="btn btn-secondary"
              onClick={handleStop}
              disabled={actionsDisabled || !running}
            >
              Stop polling/robot
            </button>
          </div>
        </div>

        {/* Demo execution controls — visually separated and warning-colored */}
        <div className="tm-control-group tm-demo-group">
          <span className="tm-control-group-label tm-demo-label">
            Demo execution (DEMO ONLY)
          </span>
          <div className="erp-warning erp-warning-strong tm-demo-warning">
            Demo only. Live trading is disabled. These controls can place orders on
            a connected MT5 DEMO account and are refused on live or unknown accounts.
          </div>
          <label className="erp-check-block">
            <input
              type="checkbox"
              checked={ackOrders}
              disabled={actionsDisabled}
              onChange={(e) => setAckOrders(e.target.checked)}
            />
            I understand this can place orders on the connected MT5 DEMO account.
          </label>
          <label className="erp-check-block">
            <input
              type="checkbox"
              checked={ackDemo}
              disabled={actionsDisabled}
              onChange={(e) => setAckDemo(e.target.checked)}
            />
            I confirm the connected account is a demo account.
          </label>
          <label className="erp-check-inline tm-minlot">
            <input
              type="checkbox"
              checked={allowMinLot}
              disabled={actionsDisabled}
              onChange={(e) => setAllowMinLot(e.target.checked)}
            />
            Allow min-lot rounding (increases risk)
          </label>
          <div className="slb-row tm-demo-actions">
            <button
              className="btn btn-danger"
              onClick={handleDemoOnce}
              disabled={!demoReady}
              title={
                demoReady
                  ? "Run one demo execution decision"
                  : "Requires a saved supported config, MT5 ready, and both confirmations"
              }
            >
              Demo execution once
            </button>
            <button
              className="btn btn-danger"
              onClick={handleStartDemo}
              disabled={!demoReady || running}
              title={
                demoReady
                  ? "Start demo execution polling"
                  : "Requires a saved supported config, MT5 ready, and both confirmations"
              }
            >
              Start demo execution polling
            </button>
          </div>
          {demoBlockedByRisk ? (
            <p className="chart-state chart-state-warn tm-demo-hint">
              The last dry-run implied more than the manual-risk ceiling. Tick
              “Allow high manual risk” in Position sizing, or lower the manual lot.
            </p>
          ) : !demoReady ? (
            <p className="slb-hint tm-demo-hint">
              Demo execution is disabled until a supported config is saved, MT5 is
              ready, both confirmations are checked, and sizing is valid.
            </p>
          ) : null}
        </div>
      </section>

      {/* E. Mini history */}
      <div className="tm-grid">
        <section className="tm-card">
          <h3 className="tm-card-title">Recent signal checks</h3>
          {recentChecks.length === 0 ? (
            <p className="slb-hint">No checks yet.</p>
          ) : (
            <table className="slb-table tm-mini-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Signal</th>
                  <th>Regime</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {recentChecks.map((row) => (
                  <tr key={row.signal_time}>
                    <td>{fmtDateTime(row.signal_time)}</td>
                    <td>
                      <span
                        className={
                          row.signal_type === "BUY"
                            ? "slb-signal slb-signal-buy"
                            : "slb-signal slb-signal-none"
                        }
                      >
                        {signalLabel(row.signal_type)}
                      </span>
                    </td>
                    <td>
                      <span className={regimeClass(row.strategy_regime)}>
                        {regimeLabel(row.strategy_regime)}
                      </span>
                    </td>
                    <td className="slb-table-reason">{row.reason_human}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="tm-card">
          <h3 className="tm-card-title">Recent execution decisions</h3>
          {execHistory.length === 0 ? (
            <p className="slb-hint">No decisions yet.</p>
          ) : (
            <table className="slb-table tm-mini-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Mode</th>
                  <th>Action</th>
                  <th>Lot</th>
                </tr>
              </thead>
              <tbody>
                {execHistory.map((row) => (
                  <tr key={row.decision_id}>
                    <td>{fmtDateTime(row.generated_at)}</td>
                    <td>{MODE_LABEL[row.mode] ?? row.mode}</td>
                    <td>{ACTION_LABEL[row.intended_action] ?? row.intended_action}</td>
                    <td>{fmtLot(row.lot)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}
