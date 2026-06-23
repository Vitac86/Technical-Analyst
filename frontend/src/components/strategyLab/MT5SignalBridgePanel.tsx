import { useCallback, useEffect, useRef, useState } from "react";

import {
  checkMt5Readiness,
  checkSignalOnce,
  fetchExportedConfig,
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
    if (data.latest_signal !== undefined) setLatest(data.latest_signal ?? null);
  }, []);

  const refreshHistory = useCallback(async () => {
    const data = await getSignalHistory(50);
    setHistory(data.signals);
    if (data.signals[0]) setLatest(data.signals[0]);
  }, []);

  // Initial load.
  useEffect(() => {
    void refreshConfigs().catch(() => undefined);
    void refreshStatus().catch(() => undefined);
    void refreshHistory().catch(() => undefined);
  }, [refreshConfigs, refreshStatus, refreshHistory]);

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

      {/* D. Latest signal */}
      <div className="slb-section">
        <h3 className="slb-section-title">Latest signal</h3>
        <div className="slb-safety-badge slb-safety-inline">
          Signal-only mode. Execution disabled.
        </div>
        {latest ? (
          <>
            {isStale(latest) ? (
              <div className="chart-state chart-state-warn">
                This signal may be stale (generated {fmtDateTime(latest.generated_at)}).
              </div>
            ) : null}
            <dl className="slb-kv slb-kv-wide">
              <div>
                <dt>Signal</dt>
                <dd>
                  <span
                    className={
                      latest.signal_type === "BUY"
                        ? "slb-signal slb-signal-buy"
                        : "slb-signal slb-signal-none"
                    }
                  >
                    {latest.signal_type}
                  </span>
                </dd>
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
                <dt>Symbol</dt>
                <dd>{latest.symbol}</dd>
              </div>
              <div>
                <dt>Timeframe</dt>
                <dd>{latest.timeframe}</dd>
              </div>
              <div>
                <dt>Strategy</dt>
                <dd>{latest.strategy_id}</dd>
              </div>
              <div>
                <dt>Close price</dt>
                <dd>{fmtCell(latest.close_price)}</dd>
              </div>
              <div>
                <dt>ATR</dt>
                <dd>{fmtCell(latest.atr_value)}</dd>
              </div>
              <div>
                <dt>Reason</dt>
                <dd>{latest.reason}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{latest.status}</dd>
              </div>
              <div>
                <dt>Execution enabled</dt>
                <dd className="slb-exec-off">
                  {String(latest.execution_enabled)}
                </dd>
              </div>
            </dl>
          </>
        ) : (
          <p className="slb-hint">No signal yet. Run a check or start polling.</p>
        )}
      </div>

      {/* E. Signal history */}
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
                  <th>Symbol</th>
                  <th>TF</th>
                  <th>Strategy</th>
                  <th>Signal</th>
                  <th>Reason</th>
                  <th>Close</th>
                  <th>Exec</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.signal_id}>
                    <td>{fmtDateTime(row.generated_at)}</td>
                    <td>{fmtDateTime(row.signal_time)}</td>
                    <td>{row.symbol}</td>
                    <td>{row.timeframe}</td>
                    <td>{row.strategy_id}</td>
                    <td>
                      <span
                        className={
                          row.signal_type === "BUY"
                            ? "slb-signal slb-signal-buy"
                            : "slb-signal slb-signal-none"
                        }
                      >
                        {row.signal_type}
                      </span>
                    </td>
                    <td>{row.reason}</td>
                    <td>{fmtCell(row.close_price)}</td>
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
