import type {
  BacktestRequest,
  BacktestResponse,
  BridgeProcessStatus,
  CheckOnceResponse,
  LatestSignalResponse,
  Mt5Readiness,
  PresetsResponse,
  RecentChecksResponse,
  SaveSignalConfigResponse,
  SignalConfigsResponse,
  SignalHistoryResponse,
  SignalLogsResponse,
  StrategyConfig,
} from "../types/strategyLab";

// Strategy Lab lives under its own namespace (not the /api/v1 client base).
const BASE_URL = import.meta.env.VITE_API_BASE_URL_SL ?? "/api/strategy-lab";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore JSON parse failure
    }
    throw new Error(detail);
  }

  return response.json() as Promise<T>;
}

export function getPresets(): Promise<PresetsResponse> {
  return request<PresetsResponse>("/presets");
}

export function runBacktest(body: BacktestRequest): Promise<BacktestResponse> {
  return request<BacktestResponse>("/backtest", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Fetch the portable strategy config and trigger a JSON file download. */
export async function exportConfig(body: BacktestRequest): Promise<void> {
  const response = await fetch(`${BASE_URL}/export-config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const err = (await response.json()) as { detail?: string };
      if (err.detail) detail = err.detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }

  const config = await response.json();
  const filename =
    `${config.strategy_id}_${config.symbol}_${config.timeframe}.json`.replace(
      /[^A-Za-z0-9_.-]/g,
      "_",
    );
  const blob = new Blob([JSON.stringify(config, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** Fetch the portable strategy config object (same as exportConfig, no download). */
export function fetchExportedConfig(
  body: BacktestRequest,
): Promise<StrategyConfig> {
  return request<StrategyConfig>("/export-config", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// v1.7.1 MT5 signal-only bridge control (no execution / no order endpoints).
// These live under /api/strategy-lab/signals (relative to BASE_URL).
// ---------------------------------------------------------------------------

/** Save the current strategy config for the signal-only bridge. */
export function saveSignalConfig(
  config: StrategyConfig,
  name?: string,
): Promise<SaveSignalConfigResponse> {
  return request<SaveSignalConfigResponse>("/signals/configs/save", {
    method: "POST",
    body: JSON.stringify({ config, name: name ?? null }),
  });
}

/** List the server-side saved configs available to the bridge. */
export function listSignalConfigs(): Promise<SignalConfigsResponse> {
  return request<SignalConfigsResponse>("/signals/configs");
}

/** Check MT5 readiness (package/terminal/symbol/rates) — never trades. */
export function checkMt5Readiness(
  configPath: string,
  bars = 500,
): Promise<Mt5Readiness> {
  return request<Mt5Readiness>("/signals/mt5-check", {
    method: "POST",
    body: JSON.stringify({ config_path: configPath, bars }),
  });
}

/** Run a single signal-only check for a saved config. */
export function checkSignalOnce(
  configPath: string,
  bars = 500,
  recentLimit = 20,
): Promise<CheckOnceResponse> {
  return request<CheckOnceResponse>("/signals/check-once", {
    method: "POST",
    body: JSON.stringify({
      config_path: configPath,
      bars,
      recent_limit: recentLimit,
    }),
  });
}

/** Start the signal-only polling bridge as a managed subprocess. */
export function startSignalBridge(
  configPath: string,
  pollSeconds = 60,
  bars = 500,
): Promise<BridgeProcessStatus> {
  return request<BridgeProcessStatus>("/signals/start", {
    method: "POST",
    body: JSON.stringify({
      config_path: configPath,
      poll_seconds: pollSeconds,
      bars,
    }),
  });
}

/** Stop the managed polling bridge process. */
export function stopSignalBridge(): Promise<BridgeProcessStatus> {
  return request<BridgeProcessStatus>("/signals/stop", { method: "POST" });
}

/** Bridge status: process state + latest signal + log excerpts. */
export function getSignalBridgeStatus(): Promise<BridgeProcessStatus> {
  return request<BridgeProcessStatus>("/signals/status");
}

/** The most recently emitted signal (or null). */
export function getLatestSignal(): Promise<LatestSignalResponse> {
  return request<LatestSignalResponse>("/signals/latest");
}

/** Up to `limit` most-recent signals (newest first). */
export function getSignalHistory(limit = 50): Promise<SignalHistoryResponse> {
  return request<SignalHistoryResponse>(`/signals/history?limit=${limit}`);
}

/** Per-candle diagnostics over the latest closed candles (newest first). */
export function getRecentChecks(limit = 20): Promise<RecentChecksResponse> {
  return request<RecentChecksResponse>(`/signals/recent-checks?limit=${limit}`);
}

/** The last `lines` of the bridge stdout/stderr logs. */
export function getSignalLogs(lines = 100): Promise<SignalLogsResponse> {
  return request<SignalLogsResponse>(`/signals/logs?lines=${lines}`);
}
