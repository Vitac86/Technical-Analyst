import type {
  BacktestRequest,
  BacktestResponse,
  PresetsResponse,
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
