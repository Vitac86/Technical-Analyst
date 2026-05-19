import { useCallback, useEffect, useRef, useState } from "react";
import { runScanner } from "../api/scanner";
import { loadWorkspace } from "../api/workspace";
import { ScannerTable } from "../components/scanner/ScannerTable";
import type { ScannerResponse, ScannerRow, ScannerInstrumentRequest } from "../types/scanner";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LS = {
  watchlist: "technicalAnalyst.scanner.watchlist",
  timeframe: "technicalAnalyst.scanner.timeframe",
  start: "technicalAnalyst.scanner.start",
  end: "technicalAnalyst.scanner.end",
  autoLoad: "technicalAnalyst.scanner.autoLoad",
  autoRefreshInterval: "technicalAnalyst.scanner.autoRefreshInterval",
} as const;

const DEFAULT_WATCHLIST = `SBER
GAZP
LKOH
ROSN
NVTK
GMKN
VTBR
AFLT`;

const TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"];
const INTRADAY_TF = new Set(["5m", "15m", "1h", "4h"]);

const AUTO_REFRESH_OPTIONS = [
  { label: "Off", value: 0 },
  { label: "5 min", value: 5 },
  { label: "15 min", value: 15 },
  { label: "30 min", value: 30 },
] as const;

// ---------------------------------------------------------------------------
// localStorage helpers
// ---------------------------------------------------------------------------

function lsGet(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function lsGetBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "true";
  } catch { return fallback; }
}
function lsGetInt(key: string, fallback: number): number {
  try {
    const v = localStorage.getItem(key);
    if (v === null) return fallback;
    const n = parseInt(v, 10);
    return isNaN(n) ? fallback : n;
  } catch { return fallback; }
}
function lsSet(key: string, value: string | boolean | number): void {
  try { localStorage.setItem(key, String(value)); } catch {}
}

// ---------------------------------------------------------------------------
// Date helpers
// ---------------------------------------------------------------------------

function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}

function defaultStartFor(tf: string): string {
  const d = new Date();
  if (INTRADAY_TF.has(tf)) {
    d.setDate(d.getDate() - 30);
  } else {
    d.setMonth(d.getMonth() - 6);
  }
  return d.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// Stale detection
// ---------------------------------------------------------------------------

const STALE_INTRADAY_MS = 2 * 60 * 60 * 1000; // 2 hours

function isStale(row: ScannerRow, timeframe: string): boolean {
  if (!row.last_timestamp) return false;
  const ts = new Date(row.last_timestamp);
  if (isNaN(ts.getTime())) return false;
  const now = new Date();
  if (INTRADAY_TF.has(timeframe)) {
    return now.getTime() - ts.getTime() > STALE_INTRADAY_MS;
  }
  // For daily: stale if last candle is from before today
  return ts.toISOString().slice(0, 10) < now.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// Watchlist parsing
// ---------------------------------------------------------------------------

interface ParsedInstrument {
  ticker: string;
  engine: string;
  market: string;
  board: string;
}

function toScannerRequest(p: ParsedInstrument): ScannerInstrumentRequest {
  return { ticker: p.ticker, engine: p.engine, market: p.market, board: p.board };
}

function parseWatchlist(text: string): ParsedInstrument[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith("#"))
    .map((line) => {
      const parts = line.split(",").map((p) => p.trim());
      const ticker = parts[0].toUpperCase();
      return {
        ticker,
        engine: parts[1] || "stock",
        market: parts[2] || "shares",
        board: parts[3] || "TQBR",
      };
    });
}

function rowsNeedingLoad(rows: ScannerRow[], timeframe: string): ScannerRow[] {
  return rows.filter(
    (r) =>
      r.status === "no_instrument" ||
      r.status === "no_candles" ||
      r.status === "no_indicators" ||
      (r.status === "ok" && isStale(r, timeframe)),
  );
}

function makeAutoLoadKey(watchlist: string, tf: string, start: string, end: string): string {
  return `${watchlist.trim()}|${tf}|${start}|${end}`;
}

// ---------------------------------------------------------------------------
// Internal types
// ---------------------------------------------------------------------------

interface LoadRow {
  ticker: string;
  status: "success" | "failed";
  message: string;
}

interface LoadProgress {
  current: number;
  total: number;
  ticker: string;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function ScannerPage() {
  // --- Persisted settings ---
  const [watchlistText, setWatchlistText] = useState(() =>
    lsGet(LS.watchlist, DEFAULT_WATCHLIST),
  );
  const [timeframe, setTimeframe] = useState(() => lsGet(LS.timeframe, "1d"));
  const [startDate, setStartDate] = useState(() => {
    const saved = localStorage.getItem(LS.start);
    return saved ?? defaultStartFor(lsGet(LS.timeframe, "1d"));
  });
  const [endDate, setEndDate] = useState(() => lsGet(LS.end, todayStr()));
  const [autoLoadEnabled, setAutoLoadEnabled] = useState(() =>
    lsGetBool(LS.autoLoad, false),
  );
  const [autoRefreshMinutes, setAutoRefreshMinutes] = useState(() =>
    lsGetInt(LS.autoRefreshInterval, 0),
  );

  // --- Runtime state ---
  const [batchRunning, setBatchRunning] = useState(false);
  const [loadProgress, setLoadProgress] = useState<LoadProgress | null>(null);
  const [loadRows, setLoadRows] = useState<LoadRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<ScannerResponse | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  const [lastScanTime, setLastScanTime] = useState<Date | null>(null);
  const [lastLoadTime, setLastLoadTime] = useState<Date | null>(null);

  const busy = batchRunning || scanning;

  // --- Refs for use in stable callbacks ---
  const watchlistRef = useRef(watchlistText);
  const timeframeRef = useRef(timeframe);
  const startDateRef = useRef(startDate);
  const endDateRef = useRef(endDate);
  const autoLoadEnabledRef = useRef(autoLoadEnabled);
  const busyRef = useRef(false);
  const autoScanRan = useRef(false);
  const autoFlowRunning = useRef(false);
  const lastAutoLoadKey = useRef("");

  // Sync refs after every render (cheap assignments, ensures closures see fresh values)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    watchlistRef.current = watchlistText;
    timeframeRef.current = timeframe;
    startDateRef.current = startDate;
    endDateRef.current = endDate;
    autoLoadEnabledRef.current = autoLoadEnabled;
    busyRef.current = busy;
  });

  // --- Persist settings to localStorage ---
  useEffect(() => { lsSet(LS.watchlist, watchlistText); }, [watchlistText]);
  useEffect(() => { lsSet(LS.timeframe, timeframe); }, [timeframe]);
  useEffect(() => { lsSet(LS.start, startDate); }, [startDate]);
  useEffect(() => { lsSet(LS.end, endDate); }, [endDate]);
  useEffect(() => { lsSet(LS.autoLoad, autoLoadEnabled); }, [autoLoadEnabled]);
  useEffect(() => { lsSet(LS.autoRefreshInterval, autoRefreshMinutes); }, [autoRefreshMinutes]);

  // Reset auto-load key when inputs change so auto-load re-checks on next flow
  useEffect(() => {
    lastAutoLoadKey.current = "";
  }, [watchlistText, timeframe, startDate, endDate]);

  // ---------------------------------------------------------------------------
  // Core async functions (stable — deps are either refs or stable setState fns)
  // ---------------------------------------------------------------------------

  const executeScan = useCallback(async (
    instruments: ParsedInstrument[],
    tf: string,
  ): Promise<ScannerResponse | null> => {
    setScanning(true);
    setScanError(null);
    try {
      const data = await runScanner({
        instruments: instruments.map(toScannerRequest),
        timeframe: tf,
        lookback: 100,
      });
      setScanResult(data);
      setLastScanTime(new Date());
      return data;
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Scan failed.");
      return null;
    } finally {
      setScanning(false);
    }
  }, []);

  const executeBatchLoad = useCallback(async (
    toLoad: ParsedInstrument[],
    tf: string,
    start: string,
    end: string,
  ): Promise<void> => {
    setBatchRunning(true);
    setLoadError(null);
    setLoadRows([]);

    const rows: LoadRow[] = [];
    for (let i = 0; i < toLoad.length; i++) {
      const inst = toLoad[i];
      setLoadProgress({ current: i + 1, total: toLoad.length, ticker: inst.ticker });
      try {
        await loadWorkspace({
          ticker: inst.ticker,
          engine: inst.engine,
          market: inst.market,
          board: inst.board,
          timeframe: tf,
          start,
          end,
          calculate_indicators: true,
        });
        rows.push({ ticker: inst.ticker, status: "success", message: "Loaded" });
      } catch (err) {
        rows.push({
          ticker: inst.ticker,
          status: "failed",
          message: err instanceof Error ? err.message : "Request failed",
        });
      }
      setLoadRows([...rows]);
    }

    setLoadProgress(null);
    setBatchRunning(false);
    setLastLoadTime(new Date());
  }, []);

  // triggerAutoFlow: scan → auto-load missing/stale → scan again
  // Guard: won't run if already busy or another auto-flow is active
  const triggerAutoFlow = useCallback(async () => {
    if (autoFlowRunning.current || busyRef.current) return;
    autoFlowRunning.current = true;
    try {
      const instruments = parseWatchlist(watchlistRef.current);
      if (instruments.length === 0) return;

      const tf = timeframeRef.current;
      const start = startDateRef.current;
      const end = endDateRef.current;

      const result = await executeScan(instruments, tf);

      if (!autoLoadEnabledRef.current || !result) return;

      const currentKey = makeAutoLoadKey(watchlistRef.current, tf, start, end);
      if (lastAutoLoadKey.current === currentKey) return;

      const toLoad = rowsNeedingLoad(result.rows, tf).map((row) => {
        const orig = instruments.find((i) => i.ticker === row.ticker);
        return {
          ticker: row.ticker,
          engine: row.engine ?? orig?.engine ?? "stock",
          market: row.market ?? orig?.market ?? "shares",
          board: row.board ?? orig?.board ?? "TQBR",
        };
      });

      if (toLoad.length === 0) {
        lastAutoLoadKey.current = currentKey;
        return;
      }

      await executeBatchLoad(toLoad, tf, start, end);
      lastAutoLoadKey.current = currentKey;
      await executeScan(instruments, tf);
    } finally {
      autoFlowRunning.current = false;
    }
  }, [executeScan, executeBatchLoad]);

  // --- Auto scan on page open (once per mount, StrictMode-safe) ---
  useEffect(() => {
    if (autoScanRan.current) return;
    autoScanRan.current = true;
    void triggerAutoFlow();
  }, [triggerAutoFlow]);

  // --- Auto-refresh timer ---
  useEffect(() => {
    if (autoRefreshMinutes === 0) return;
    const id = setInterval(() => { void triggerAutoFlow(); }, autoRefreshMinutes * 60 * 1000);
    return () => clearInterval(id);
  }, [autoRefreshMinutes, triggerAutoFlow]);

  // ---------------------------------------------------------------------------
  // User-triggered handlers
  // ---------------------------------------------------------------------------

  function handleTimeframeChange(tf: string) {
    const wasIntraday = INTRADAY_TF.has(timeframe);
    const isIntraday = INTRADAY_TF.has(tf);
    setTimeframe(tf);
    if (wasIntraday !== isIntraday) setStartDate(defaultStartFor(tf));
  }

  async function handleScanOnly() {
    if (busy) return;
    const instruments = parseWatchlist(watchlistText);
    if (instruments.length === 0) { setScanError("Enter at least one ticker."); return; }
    setScanError(null);
    await executeScan(instruments, timeframe);
  }

  async function handleLoad() {
    if (busy) return;
    const instruments = parseWatchlist(watchlistText);
    if (instruments.length === 0) { setLoadError("Enter at least one ticker."); return; }
    setScanResult(null);
    setScanError(null);
    // Mark key as done for this config so auto-load won't repeat right after
    lastAutoLoadKey.current = makeAutoLoadKey(watchlistText, timeframe, startDate, endDate);
    await executeBatchLoad(instruments, timeframe, startDate, endDate);
    await executeScan(instruments, timeframe);
  }

  // ---------------------------------------------------------------------------
  // Derived display values
  // ---------------------------------------------------------------------------

  function modeSummary(): string {
    if (autoRefreshMinutes > 0 && autoLoadEnabled)
      return `Auto-refresh ${autoRefreshMinutes} min + auto-load`;
    if (autoRefreshMinutes > 0) return `Auto-refresh every ${autoRefreshMinutes} min`;
    if (autoLoadEnabled) return "Auto scan + auto-load";
    return "Auto scan";
  }

  const rowStats = scanResult
    ? {
        ok: scanResult.rows.filter((r) => r.status === "ok" && !isStale(r, timeframe)).length,
        stale: scanResult.rows.filter((r) => r.status === "ok" && isStale(r, timeframe)).length,
        noCandles: scanResult.rows.filter((r) => r.status === "no_candles").length,
        noIndicators: scanResult.rows.filter((r) => r.status === "no_indicators").length,
        noInstrument: scanResult.rows.filter((r) => r.status === "no_instrument").length,
        errors: scanResult.rows.filter((r) => r.status === "error").length,
      }
    : null;

  const showEmpty =
    !scanResult && !scanning && !scanError && !batchRunning && loadRows.length === 0;

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="page-content">
      <div className="page-header">
        <h2>Market Scanner</h2>
        <p className="page-subtitle">
          Load candles for a watchlist, then scan technical signals.
        </p>
      </div>

      {/* Controls */}
      <div className="scanner-controls">
        {/* Watchlist textarea */}
        <div className="scanner-watchlist-wrap">
          <label className="form-label" htmlFor="watchlist-input">
            Watchlist
          </label>
          <textarea
            id="watchlist-input"
            className="scanner-watchlist-textarea"
            value={watchlistText}
            onChange={(e) => setWatchlistText(e.target.value)}
            rows={10}
            spellCheck={false}
            disabled={busy}
          />
          <p className="scanner-hint">
            One ticker per line.{" "}
            <code>TICKER,engine,market,board</code> also accepted.
          </p>
        </div>

        {/* Options column */}
        <div className="scanner-options">
          {/* Timeframe */}
          <div className="form-field">
            <span className="form-label">Timeframe</span>
            <div className="timeframe-selector">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  className={`tf-btn${timeframe === tf ? " tf-btn-active" : ""}`}
                  onClick={() => handleTimeframeChange(tf)}
                  disabled={busy}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>

          {/* Date range */}
          <div className="scanner-date-row">
            <div className="form-field">
              <label className="form-label" htmlFor="scanner-start-date">From</label>
              <input
                id="scanner-start-date"
                type="date"
                className="date-input"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                disabled={busy}
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="scanner-end-date">To</label>
              <input
                id="scanner-end-date"
                type="date"
                className="date-input"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                disabled={busy}
              />
            </div>
          </div>

          {/* Auto-load checkbox */}
          <div className="scanner-auto-options">
            <label className="scanner-checkbox-label">
              <input
                type="checkbox"
                checked={autoLoadEnabled}
                onChange={(e) => setAutoLoadEnabled(e.target.checked)}
                disabled={busy}
              />
              Auto-load missing / stale data
            </label>
          </div>

          {/* Auto-refresh selector */}
          <div className="form-field">
            <span className="form-label">Auto-refresh</span>
            <div className="timeframe-selector">
              {AUTO_REFRESH_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  className={`tf-btn${autoRefreshMinutes === opt.value ? " tf-btn-active" : ""}`}
                  onClick={() => setAutoRefreshMinutes(opt.value)}
                  disabled={busy}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Action buttons */}
          <div className="scanner-actions">
            <button className="btn btn-primary" onClick={handleLoad} disabled={busy}>
              {batchRunning
                ? loadProgress
                  ? `Loading ${loadProgress.current} / ${loadProgress.total}: ${loadProgress.ticker}`
                  : "Loading…"
                : "Load / update watchlist"}
            </button>
            <button className="btn btn-secondary" onClick={handleScanOnly} disabled={busy}>
              {scanning ? "Scanning…" : "Scan now"}
            </button>
          </div>
        </div>
      </div>

      {/* Status bar */}
      <div className="scanner-status-bar">
        <span className="scanner-mode-chip">{modeSummary()}</span>
        {lastScanTime !== null ? (
          <span className="scanner-status-item">
            Scanned {lastScanTime.toLocaleTimeString("ru-RU")}
          </span>
        ) : null}
        {lastLoadTime !== null ? (
          <span className="scanner-status-item">
            Loaded {lastLoadTime.toLocaleTimeString("ru-RU")}
          </span>
        ) : null}
        {rowStats !== null ? (
          <span className="scanner-status-item">
            {rowStats.ok} ok
            {rowStats.stale > 0 ? ` · ${rowStats.stale} stale` : ""}
            {rowStats.noCandles > 0 ? ` · ${rowStats.noCandles} no candles` : ""}
            {rowStats.noIndicators > 0 ? ` · ${rowStats.noIndicators} no indicators` : ""}
            {rowStats.noInstrument > 0 ? ` · ${rowStats.noInstrument} not found` : ""}
            {rowStats.errors > 0 ? ` · ${rowStats.errors} errors` : ""}
          </span>
        ) : null}
      </div>

      {/* Load error */}
      {loadError !== null ? (
        <div className="chart-state chart-state-error">{loadError}</div>
      ) : null}

      {/* Load results panel */}
      {loadRows.length > 0 || loadProgress !== null ? (
        <div className="scanner-load-panel">
          <div className="scanner-load-header">
            <span className="form-label">Load results</span>
            {loadProgress !== null ? (
              <span className="scanner-load-progress">
                {loadProgress.current} / {loadProgress.total} — {loadProgress.ticker}
              </span>
            ) : (
              <span className="scanner-load-done">
                {loadRows.filter((r) => r.status === "success").length} /{" "}
                {loadRows.length} succeeded
              </span>
            )}
          </div>
          <div className="scanner-load-list">
            {loadRows.map((row) => (
              <div
                key={row.ticker}
                className={`scanner-load-row scanner-load-row-${row.status}`}
              >
                <span className="scanner-load-ticker">{row.ticker}</span>
                <span className="scanner-load-status">
                  {row.status === "success" ? "OK" : "Failed"}
                </span>
                <span className="scanner-load-message">{row.message}</span>
              </div>
            ))}
            {loadProgress !== null ? (
              <div className="scanner-load-row scanner-load-row-pending">
                <span className="scanner-load-ticker">{loadProgress.ticker}</span>
                <span className="scanner-load-status">Loading…</span>
                <span className="scanner-load-message" />
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* Scan error */}
      {scanError !== null ? (
        <div className="chart-state chart-state-error">{scanError}</div>
      ) : null}

      {/* Scanner results */}
      {scanResult !== null ? (
        <div className="scanner-results">
          <div className="scanner-results-header">
            <span className="scanner-results-meta">
              {scanResult.rows.length} instrument
              {scanResult.rows.length !== 1 ? "s" : ""} · {scanResult.timeframe} ·{" "}
              {new Date(scanResult.generated_at).toLocaleTimeString("ru-RU")}
            </span>
            <span className="ts-disclaimer">
              Research signals only — not financial advice.
            </span>
          </div>
          <ScannerTable rows={scanResult.rows} />
        </div>
      ) : null}

      {showEmpty ? (
        <div className="chart-state">
          {scanning ? "Scanning…" : "Auto scan starting…"}
        </div>
      ) : null}
    </div>
  );
}
