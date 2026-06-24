import { useCallback, useEffect, useRef, useState } from "react";

import {
  executionDemoOnce,
  executionDryRunOnce,
  fetchExportedConfig,
  getExecutionHistory,
  getExecutionLogs,
  getExecutionStatus,
  listExecutionConfigs,
  saveExecutionConfig,
  startExecutionRobot,
  stopExecutionRobot,
} from "../../api/strategyLab";
import type {
  BacktestRequest,
  ExecutionDecision,
  ExecutionHistoryRow,
  ExecutionLogsResponse,
  ExecutionSavedConfig,
  ExecutionStatus,
} from "../../types/strategyLab";
import { fmtDateTime } from "./format";

interface Props {
  /** Build the current Strategy Lab config request (preset + overrides + costs). */
  buildConfigBody: () => BacktestRequest;
  /** Disabled while a backtest is running on the page. */
  disabled?: boolean;
}

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

/** Action wording is deliberately demo-only (never "trade now" / "go live"). */
const ACTION_LABEL: Record<string, string> = {
  no_action: "No action",
  would_open_buy: "Would open BUY",
  opened_buy: "Opened BUY on demo",
  would_update_trailing_sl: "Would update trailing SL",
  updated_trailing_sl: "Trailing SL updated on demo",
  refused: "Refused",
};

function actionClass(action: string): string {
  if (action === "opened_buy" || action === "updated_trailing_sl") {
    return "erp-bigbadge erp-bigbadge-exec";
  }
  if (action === "would_open_buy" || action === "would_update_trailing_sl") {
    return "erp-bigbadge erp-bigbadge-would";
  }
  if (action === "refused") return "erp-bigbadge erp-bigbadge-refused";
  return "erp-bigbadge erp-bigbadge-none";
}

const MODE_LABEL: Record<string, string> = {
  dry_run: "Dry-run",
  demo_execution: "Demo execution",
  refused: "Refused",
};

const REFUSAL_LABEL: Record<string, string> = {
  account_is_not_demo_live_or_unknown:
    "Connected account is not a detected demo account (live or unknown).",
  execution_not_enabled: "Execution is not enabled.",
  demo_only_flag_required: "Demo-only safety flag is required.",
  demo_execution_not_confirmed: "Demo execution was not confirmed.",
  lot_below_minimum: "Computed lot is below the symbol minimum.",
  margin_insufficient: "Free margin is insufficient for the computed lot.",
  invalid_lot_sizing: "Lot sizing could not be computed (missing inputs).",
  order_send_failed: "MT5 rejected the order (see order result).",
  live_execution_not_supported_in_v1_8: "Live execution is not implemented in v1.8.",
};

function refusalText(reason: string): string {
  return REFUSAL_LABEL[reason] ?? reason;
}

/** A safety badge for the checklist. */
function Badge({ ok, label }: { ok: boolean | null; label: string }) {
  const cls =
    ok === null
      ? "erp-badge erp-badge-idle"
      : ok
        ? "erp-badge erp-badge-ok"
        : "erp-badge erp-badge-err";
  const mark = ok === null ? "•" : ok ? "✓" : "✕";
  return (
    <span className={cls}>
      {mark} {label}
    </span>
  );
}

export function MT5ExecutionRobotPanel({ buildConfigBody, disabled }: Props) {
  const [open, setOpen] = useState(false); // collapsed by default

  const [configs, setConfigs] = useState<ExecutionSavedConfig[]>([]);
  const [selectedPath, setSelectedPath] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);

  const [pollSeconds, setPollSeconds] = useState(60);
  const [allowMinLot, setAllowMinLot] = useState(false);

  // Two explicit confirmations are required to arm demo execution.
  const [ackOrders, setAckOrders] = useState(false);
  const [ackDemo, setAckDemo] = useState(false);

  const [decision, setDecision] = useState<ExecutionDecision | null>(null);
  const [status, setStatus] = useState<ExecutionStatus | null>(null);
  const [history, setHistory] = useState<ExecutionHistoryRow[]>([]);

  const [logs, setLogs] = useState<ExecutionLogsResponse | null>(null);
  const [logsOpen, setLogsOpen] = useState(false);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const running = status?.running ?? false;
  const selected = configs.find((c) => c.path === selectedPath) ?? null;
  const selectedSupported = selected?.is_supported ?? false;
  const hasConfig = Boolean(selectedPath) && selectedSupported;
  const actionsDisabled = disabled || busy;
  const demoArmed = ackOrders && ackDemo;

  const refreshConfigs = useCallback(async () => {
    const data = await listExecutionConfigs();
    setConfigs(data.configs);
    setSelectedPath((prev) => {
      if (prev && data.configs.some((c) => c.path === prev)) return prev;
      const firstSupported = data.configs.find((c) => c.is_supported);
      return firstSupported?.path ?? data.configs[0]?.path ?? "";
    });
  }, []);

  const refreshStatus = useCallback(async () => {
    const data = await getExecutionStatus();
    setStatus(data);
    if (data.latest_execution_decision !== undefined) {
      setDecision(data.latest_execution_decision ?? null);
    }
  }, []);

  const refreshHistory = useCallback(async () => {
    const data = await getExecutionHistory(50);
    setHistory(data.events);
  }, []);

  // Initial load (only once the panel is opened, to avoid needless calls).
  useEffect(() => {
    if (!open) return;
    void refreshConfigs().catch(() => undefined);
    void refreshStatus().catch(() => undefined);
    void refreshHistory().catch(() => undefined);
  }, [open, refreshConfigs, refreshStatus, refreshHistory]);

  // Poll status/history while the robot is running.
  useEffect(() => {
    if (!open || !running) return;
    const id = window.setInterval(() => {
      void refreshStatus().catch(() => undefined);
      void refreshHistory().catch(() => undefined);
    }, 15000);
    return () => window.clearInterval(id);
  }, [open, running, refreshStatus, refreshHistory]);

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
        const saved = await saveExecutionConfig(config);
        setSavedNote(`Saved ${saved.file_name}`);
        await refreshConfigs();
        setSelectedPath(saved.path);
      } finally {
        setSaving(false);
      }
    });

  const handleDryRun = () =>
    guard("Dry-run", async () => {
      const result = await executionDryRunOnce(selectedPath, {
        allowMinLotRounding: allowMinLot,
      });
      setDecision(result);
      await refreshHistory();
    });

  const handleDemoOnce = () =>
    guard("Demo execution", async () => {
      const result = await executionDemoOnce(selectedPath, demoArmed, {
        allowMinLotRounding: allowMinLot,
      });
      setDecision(result);
      await refreshHistory();
      await refreshStatus();
    });

  const handleStartDryRun = () =>
    guard("Start dry-run polling", async () => {
      const result = await startExecutionRobot({
        configPath: selectedPath,
        pollSeconds,
        allowMinLotRounding: allowMinLot,
      });
      setStatus(result);
      if (result.message && result.started === false) setSavedNote(result.message);
    });

  const handleStartDemo = () =>
    guard("Start demo execution polling", async () => {
      const result = await startExecutionRobot({
        configPath: selectedPath,
        pollSeconds,
        demoExecutionEnabled: true,
        confirmDemoExecution: demoArmed,
        allowMinLotRounding: allowMinLot,
      });
      setStatus(result);
      if (result.message && result.started === false) setSavedNote(result.message);
    });

  const handleStop = () =>
    guard("Stop robot", async () => {
      const result = await stopExecutionRobot();
      setStatus(result);
    });

  const handleRefreshLogs = () =>
    guard("Refresh logs", async () => {
      const data = await getExecutionLogs(100);
      setLogs(data);
      setLogsOpen(true);
    });

  // Derived views of the latest decision.
  const account = decision?.account;
  const sizing = decision?.sizing;
  const position = decision?.position_state;
  const order = decision?.order_result;
  const trailing = decision?.trailing;
  const mlDisabled = selected
    ? selected.ml_filter_enabled === false
    : null;

  return (
    <section className="panel slb-panel erp-panel">
      <div className="panel-header">
        <button
          type="button"
          className="erp-collapse-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <span className="erp-caret">{open ? "▾" : "▸"}</span>
          <h2>MT5 Demo Execution Robot</h2>
        </button>
        <span className="erp-danger-badge">DEMO ONLY · DRY-RUN DEFAULT</span>
      </div>

      <div className="erp-warning">
        ⚠️ Demo execution only. Live trading is disabled. Use at your own risk.
        Dry-run is the default. The robot opens a BUY only on a fresh D SuperTrend
        H4 signal on a <strong>detected demo account</strong>, trails the stop
        upward, and never closes a position.
      </div>

      {!open ? null : (
        <>
          {error ? (
            <div className="chart-state chart-state-error">{error}</div>
          ) : null}

          {/* A. Config */}
          <div className="slb-section">
            <h3 className="slb-section-title">Config</h3>
            <div className="slb-row">
              <button
                className="btn btn-primary"
                onClick={handleSave}
                disabled={actionsDisabled || saving}
              >
                {saving ? "Saving…" : "Save current config for robot"}
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
              <span className="sl-field-label">Saved configs (shared with Signal Bridge)</span>
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

            {selected ? (
              <div className="erp-config-summary">
                <span>
                  {selected.strategy_id} · {selected.symbol} {selected.timeframe}
                </span>
                {selectedSupported ? (
                  <span className="erp-badge erp-badge-ok">D supported</span>
                ) : (
                  <span className="erp-badge erp-badge-err">
                    Execution supports only D SuperTrend H4 in v1.8.
                  </span>
                )}
              </div>
            ) : (
              <p className="slb-hint">Save current config for the robot first.</p>
            )}
          </div>

          {/* B. Safety checklist */}
          <div className="slb-section">
            <h3 className="slb-section-title">Safety checklist</h3>
            <div className="erp-checklist">
              <Badge ok={status ? account != null : null} label="MT5 connected" />
              <Badge
                ok={account ? account.login != null : null}
                label="Account detected"
              />
              <Badge ok={account ? account.is_demo : null} label="Demo account" />
              <Badge ok={selectedSupported} label="Strategy D supported" />
              <Badge
                ok={selected ? selectedSupported : null}
                label="Direction long_only"
              />
              <Badge ok={mlDisabled} label="ML disabled" />
              <Badge
                ok={decision ? decision.mode !== "demo_execution" : true}
                label={
                  decision?.mode === "demo_execution"
                    ? "Mode: demo execution"
                    : "Mode: dry-run"
                }
              />
              <Badge
                ok={position ? !position.has_position : null}
                label="One-position-only"
              />
            </div>
            <p className="erp-checklist-note">
              Badges reflect the most recent decision. Run a dry-run to refresh
              the connection / account / position state.
            </p>
          </div>

          {/* C. Dry-run */}
          <div className="slb-section">
            <h3 className="slb-section-title">Dry-run</h3>
            <p className="erp-mode-note">
              Dry-run never sends an order. It computes what <em>would</em> happen
              and is safe to run on any account.
            </p>
            <div className="slb-row">
              <button
                className="btn btn-secondary"
                onClick={handleDryRun}
                disabled={actionsDisabled || !hasConfig}
              >
                Dry-run once
              </button>
              <label className="erp-check-inline">
                <input
                  type="checkbox"
                  checked={allowMinLot}
                  disabled={actionsDisabled}
                  onChange={(e) => setAllowMinLot(e.target.checked)}
                />
                Allow min-lot rounding (increases risk)
              </label>
            </div>
          </div>

          {/* D. Demo execution controls */}
          <div className="slb-section erp-demo-section">
            <h3 className="slb-section-title">Demo execution controls</h3>
            <div className="erp-warning erp-warning-strong">
              These controls can place orders on the connected MT5 DEMO account.
              They are refused on live or unknown accounts.
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

            <div className="slb-row erp-demo-actions">
              <button
                className="btn btn-danger"
                onClick={handleDemoOnce}
                disabled={actionsDisabled || !hasConfig || !demoArmed}
                title={
                  demoArmed
                    ? "Run one demo execution decision"
                    : "Tick both confirmations to enable"
                }
              >
                Demo execution once
              </button>
            </div>

            <div className="slb-row erp-poll-row">
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
                onClick={handleStartDryRun}
                disabled={actionsDisabled || !hasConfig || running}
              >
                Start dry-run polling
              </button>
              <button
                className="btn btn-danger"
                onClick={handleStartDemo}
                disabled={actionsDisabled || !hasConfig || running || !demoArmed}
              >
                Start demo execution polling
              </button>
              <button
                className="btn btn-secondary"
                onClick={handleStop}
                disabled={actionsDisabled || !running}
              >
                Stop robot
              </button>
            </div>

            <div className="slb-status-line">
              <span
                className={
                  running ? "erp-badge erp-badge-ok" : "erp-badge erp-badge-idle"
                }
              >
                {running ? `Running (${status?.mode ?? "dry_run"})` : "Stopped"}
              </span>
              {status?.pid ? <span>pid {status.pid}</span> : null}
              {status?.started_at ? (
                <span>since {fmtDateTime(status.started_at)}</span>
              ) : null}
              {status?.poll_seconds ? (
                <span>every {status.poll_seconds}s</span>
              ) : null}
            </div>
          </div>

          {/* E. Latest decision card */}
          <div className="slb-section">
            <h3 className="slb-section-title">Latest decision</h3>
            {decision ? (
              <div className="erp-decision">
                <div className="erp-decision-head">
                  <span className={actionClass(decision.intended_action)}>
                    {ACTION_LABEL[decision.intended_action] ??
                      decision.intended_action}
                  </span>
                  <span className="erp-mode-chip">
                    {MODE_LABEL[decision.mode] ?? decision.mode}
                  </span>
                  <span className="erp-decision-time">
                    {fmtDateTime(decision.generated_at)}
                  </span>
                </div>

                {decision.refusal_reasons.length > 0 ? (
                  <ul className="erp-refusals">
                    {decision.refusal_reasons.map((r) => (
                      <li key={r}>{refusalText(r)}</li>
                    ))}
                  </ul>
                ) : null}

                {decision.notes.length > 0 ? (
                  <p className="erp-notes">{decision.notes.join(" · ")}</p>
                ) : null}

                <dl className="slb-kv slb-kv-wide">
                  <div>
                    <dt>Signal</dt>
                    <dd>
                      {decision.signal.signal_type ?? "—"}
                      {decision.signal.strategy_regime
                        ? ` · ${decision.signal.strategy_regime}`
                        : ""}
                    </dd>
                  </div>
                  <div>
                    <dt>Signal time</dt>
                    <dd>{fmtDateTime(decision.signal.signal_time)}</dd>
                  </div>
                  <div>
                    <dt>Reason</dt>
                    <dd>{decision.signal.reason_human ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Entry price (ask)</dt>
                    <dd>{fmtCell(sizing?.entry_price)}</dd>
                  </div>
                  <div>
                    <dt>Initial SL</dt>
                    <dd>{fmtCell(sizing?.initial_stop_price)}</dd>
                  </div>
                  <div>
                    <dt>Lot</dt>
                    <dd>
                      {fmtLot(sizing?.rounded_lot)}
                      {sizing?.increased_risk_due_to_min_lot
                        ? " (min-lot ↑risk)"
                        : ""}
                    </dd>
                  </div>
                  <div>
                    <dt>Risk amount</dt>
                    <dd>{fmtCell(sizing?.risk_amount)}</dd>
                  </div>
                  <div>
                    <dt>Required margin</dt>
                    <dd>{fmtCell(sizing?.required_margin)}</dd>
                  </div>
                  <div>
                    <dt>Free margin</dt>
                    <dd>{fmtCell(sizing?.free_margin)}</dd>
                  </div>
                  <div>
                    <dt>Account</dt>
                    <dd>
                      {account?.trade_mode ?? "—"}
                      {account?.is_demo ? " (demo)" : ""}
                    </dd>
                  </div>
                  <div>
                    <dt>Equity</dt>
                    <dd>{fmtCell(account?.equity)}</dd>
                  </div>
                  <div>
                    <dt>Position</dt>
                    <dd>
                      {position?.has_position
                        ? `BUY ${fmtLot(position.volume)} @ ${fmtCell(
                            position.price_open,
                          )}`
                        : "none"}
                    </dd>
                  </div>
                </dl>

                {trailing && trailing.trailing_stop_candidate != null ? (
                  <div className="erp-subcard">
                    <h4>Trailing SL</h4>
                    <dl className="slb-kv slb-kv-wide">
                      <div>
                        <dt>Current SL</dt>
                        <dd>{fmtCell(trailing.current_sl)}</dd>
                      </div>
                      <div>
                        <dt>Candidate</dt>
                        <dd>{fmtCell(trailing.trailing_stop_candidate)}</dd>
                      </div>
                      <div>
                        <dt>Would improve</dt>
                        <dd>{trailing.would_improve_sl ? "yes" : "no"}</dd>
                      </div>
                      <div>
                        <dt>Update sent</dt>
                        <dd>{trailing.update_sent ? "yes" : "no"}</dd>
                      </div>
                    </dl>
                  </div>
                ) : null}

                {order ? (
                  <div className="erp-subcard">
                    <h4>Order result</h4>
                    <dl className="slb-kv slb-kv-wide">
                      <div>
                        <dt>Retcode</dt>
                        <dd>{order.retcode ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>Order</dt>
                        <dd>{order.order ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>Deal</dt>
                        <dd>{order.deal ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>Price</dt>
                        <dd>{fmtCell(order.price)}</dd>
                      </div>
                      <div>
                        <dt>Volume</dt>
                        <dd>{fmtLot(order.volume)}</dd>
                      </div>
                      <div>
                        <dt>Message</dt>
                        <dd>{order.message ?? order.comment ?? "—"}</dd>
                      </div>
                    </dl>
                  </div>
                ) : null}
              </div>
            ) : (
              <p className="slb-hint">
                No decision yet. Run <strong>Dry-run once</strong> to see what the
                robot would do.
              </p>
            )}
          </div>

          {/* F. Execution history */}
          <div className="slb-section">
            <h3 className="slb-section-title">Execution history</h3>
            {history.length === 0 ? (
              <p className="slb-hint">No decisions recorded yet.</p>
            ) : (
              <div className="slb-table-wrap">
                <table className="slb-table">
                  <thead>
                    <tr>
                      <th>Generated at</th>
                      <th>Mode</th>
                      <th>Action</th>
                      <th>Signal time</th>
                      <th>Signal</th>
                      <th>Symbol</th>
                      <th>Lot</th>
                      <th>Entry</th>
                      <th>Initial SL</th>
                      <th>Retcode</th>
                      <th>Refusals</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((row) => (
                      <tr key={row.decision_id}>
                        <td>{fmtDateTime(row.generated_at)}</td>
                        <td>{MODE_LABEL[row.mode] ?? row.mode}</td>
                        <td>{ACTION_LABEL[row.intended_action] ?? row.intended_action}</td>
                        <td>{fmtDateTime(row.signal_time)}</td>
                        <td>{row.signal_type || "—"}</td>
                        <td>{row.symbol || "—"}</td>
                        <td>{fmtLot(row.lot)}</td>
                        <td>{fmtCell(row.entry_price)}</td>
                        <td>{fmtCell(row.initial_stop_price)}</td>
                        <td>{row.order_retcode || "—"}</td>
                        <td className="slb-table-reason">
                          {row.refusal_reasons || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* G. Logs */}
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
        </>
      )}
    </section>
  );
}
