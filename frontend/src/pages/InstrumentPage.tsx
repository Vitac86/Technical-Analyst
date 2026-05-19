import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { IndicatorPanel } from "../components/charts/IndicatorPanel";
import { PriceChart, type ChartOverlays } from "../components/charts/PriceChart";
import { TechnicalSignalsPanel } from "../components/analysis/TechnicalSignalsPanel";
import { TechnicalLevelsPanel } from "../components/analysis/TechnicalLevelsPanel";
import { QuoteSummaryCard } from "../components/quotes/QuoteSummaryCard";
import { getCandles } from "../api/candles";
import { getIndicatorValues } from "../api/indicators";
import { searchInstruments } from "../api/instruments";
import { loadWorkspace } from "../api/workspace";
import { getTechnicalSignals, getTechnicalLevels } from "../api/analysis";
import { getMoexQuote } from "../api/quotes";
import type { Candle } from "../types/candle";
import type { IndicatorValue } from "../types/indicator";
import type { InstrumentSearchResult } from "../types/instrument";
import type { LastPriceSummary, WorkspaceLoadResponse } from "../types/workspace";
import type { TechnicalSignalResponse } from "../types/analysis";
import type { TechnicalLevelsResponse } from "../types/levels";
import type { QuoteSnapshot } from "../types/quote";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"] as const;
type Timeframe = (typeof TIMEFRAMES)[number];

const INDICATOR_NAMES = [
  "sma_20",
  "ema_20",
  "bollinger_bands_20_2",
  "rsi_14",
  "macd_12_26_9",
] as const;
type IndicatorName = (typeof INDICATOR_NAMES)[number];
type IndicatorMap = Record<IndicatorName, IndicatorValue[]>;

type InstrumentSource = {
  ticker: string;
  engine: string;
  market: string;
  board: string;
};

const QUOTE_REFRESH_OPTIONS: { label: string; value: number | null }[] = [
  { label: "Off", value: null },
  { label: "15 sec", value: 15 },
  { label: "30 sec", value: 30 },
  { label: "60 sec", value: 60 },
];

// ---------------------------------------------------------------------------
// localStorage helpers
// ---------------------------------------------------------------------------

const LS_PREFIX = "technicalAnalyst.chart.";

function lsGet(key: string): string | null {
  try {
    return localStorage.getItem(LS_PREFIX + key);
  } catch {
    return null;
  }
}

function lsSet(key: string, value: string): void {
  try {
    localStorage.setItem(LS_PREFIX + key, value);
  } catch {
    // storage might be unavailable
  }
}

// ---------------------------------------------------------------------------
// Utility / pure functions
// ---------------------------------------------------------------------------

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function defaultStartDate(tf: Timeframe): string {
  const d = new Date();
  if (tf === "1d") {
    d.setMonth(d.getMonth() - 6);
  } else {
    d.setDate(d.getDate() - 30);
  }
  return d.toISOString().slice(0, 10);
}

function candleRefreshMs(tf: Timeframe): number {
  const map: Record<Timeframe, number> = {
    "5m": 60_000,
    "15m": 2 * 60_000,
    "1h": 5 * 60_000,
    "4h": 10 * 60_000,
    "1d": 15 * 60_000,
  };
  return map[tf];
}

function createEmptyIndicators(): IndicatorMap {
  return {
    sma_20: [],
    ema_20: [],
    bollinger_bands_20_2: [],
    rsi_14: [],
    macd_12_26_9: [],
  };
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function formatChange(change: number | null, pct: number | null): string {
  if (change === null || pct === null) return "";
  const sign = change >= 0 ? "+" : "";
  return `${sign}${change.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
}

function formatTime(d: Date | null): string {
  return d ? d.toLocaleTimeString("ru-RU") : "—";
}

// ---------------------------------------------------------------------------
// State initializers (read from localStorage with defaults)
// ---------------------------------------------------------------------------

function initSource(routeTicker?: string): InstrumentSource {
  return {
    ticker: (lsGet("ticker") ?? routeTicker?.toUpperCase() ?? "SBER").toUpperCase(),
    engine: lsGet("engine") ?? "stock",
    market: lsGet("market") ?? "shares",
    board: lsGet("board") ?? "TQBR",
  };
}

function initTimeframe(): Timeframe {
  return (lsGet("timeframe") as Timeframe | null) ?? "1d";
}

function initStartDate(): string {
  const saved = lsGet("start");
  if (saved) return saved;
  return defaultStartDate((lsGet("timeframe") as Timeframe | null) ?? "1d");
}

function initQuoteRefreshSeconds(): number | null {
  const v = lsGet("quoteRefreshSeconds");
  if (v === null) return 15;
  if (v === "off") return null;
  const n = parseInt(v, 10);
  return isNaN(n) ? 15 : n;
}

function initBool(key: string, defaultVal = true): boolean {
  const v = lsGet(key);
  if (v === null) return defaultVal;
  return v !== "false";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function InstrumentPage() {
  const { ticker: routeTicker } = useParams<{ ticker?: string }>();

  // ── Settings state (persisted to localStorage) ──────────────────────────
  const [source, setSource] = useState<InstrumentSource>(() => initSource(routeTicker));
  const [timeframe, setTimeframe] = useState<Timeframe>(initTimeframe);
  const [startDate, setStartDate] = useState<string>(initStartDate);
  const [endDate, setEndDate] = useState<string>(() => lsGet("end") ?? today());
  const [autoMode, setAutoMode] = useState<boolean>(() => lsGet("autoMode") !== "false");
  const [quoteRefreshSeconds, setQuoteRefreshSeconds] = useState<number | null>(
    initQuoteRefreshSeconds,
  );
  const [candleRefreshEnabled, setCandleRefreshEnabled] = useState<boolean>(
    () => initBool("candleRefreshEnabled"),
  );

  // ── Overlay toggles (persisted to localStorage) ─────────────────────────
  const [showSma,       setShowSma]       = useState(() => initBool("showSma"));
  const [showEma,       setShowEma]       = useState(() => initBool("showEma"));
  const [showBollinger, setShowBollinger] = useState(() => initBool("showBollinger"));
  const [showLevels,    setShowLevels]    = useState(() => initBool("showLevels"));
  const [showVolume,    setShowVolume]    = useState(() => initBool("showVolume"));

  // ── Search ───────────────────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState(source.ticker);
  const [searchResults, setSearchResults] = useState<InstrumentSearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchBoxRef = useRef<HTMLDivElement>(null);

  // ── Workspace / chart data ───────────────────────────────────────────────
  const [lastWorkspace, setLastWorkspace] = useState<WorkspaceLoadResponse | null>(null);
  const [lastPrice, setLastPrice] = useState<LastPriceSummary | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [indicators, setIndicators] = useState<IndicatorMap>(createEmptyIndicators);
  const [candleCount, setCandleCount] = useState(0);

  // ── Analysis ─────────────────────────────────────────────────────────────
  const [signals, setSignals] = useState<TechnicalSignalResponse | null>(null);
  const [signalsLoading, setSignalsLoading] = useState(false);
  const [signalsError, setSignalsError] = useState<string | null>(null);
  const [levels, setLevels] = useState<TechnicalLevelsResponse | null>(null);
  const [levelsLoading, setLevelsLoading] = useState(false);
  const [levelsError, setLevelsError] = useState<string | null>(null);

  // ── Quote ────────────────────────────────────────────────────────────────
  const [quote, setQuote] = useState<QuoteSnapshot | null>(null);
  const [quoteLoaded, setQuoteLoaded] = useState(false);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [lastQuoteTime, setLastQuoteTime] = useState<Date | null>(null);
  const [lastCandleSyncTime, setLastCandleSyncTime] = useState<Date | null>(null);

  // ── UI ───────────────────────────────────────────────────────────────────
  const [isUpdating, setIsUpdating] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // ── Stable refs (for polling closures) ──────────────────────────────────
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const timeframeRef = useRef(timeframe);
  timeframeRef.current = timeframe;
  const startDateRef = useRef(startDate);
  startDateRef.current = startDate;
  const endDateRef = useRef(endDate);
  endDateRef.current = endDate;
  const autoModeRef = useRef(autoMode);
  autoModeRef.current = autoMode;

  const isWorkspaceLoadingRef = useRef(false);
  const isQuoteLoadingRef = useRef(false);
  const initDoneRef = useRef(false);

  // ── Core async functions ─────────────────────────────────────────────────

  async function reloadSignals(id: number, tf: Timeframe) {
    setSignalsLoading(true);
    setSignalsError(null);
    try {
      setSignals(await getTechnicalSignals(id, tf));
    } catch (err) {
      setSignalsError(errorMessage(err, "Failed to load signals."));
      setSignals(null);
    } finally {
      setSignalsLoading(false);
    }
  }

  async function reloadLevels(id: number, tf: Timeframe) {
    setLevelsLoading(true);
    setLevelsError(null);
    try {
      setLevels(await getTechnicalLevels(id, tf));
    } catch (err) {
      setLevelsError(errorMessage(err, "Failed to load levels."));
      setLevels(null);
    } finally {
      setLevelsLoading(false);
    }
  }

  async function fetchQuote(src: InstrumentSource) {
    if (isQuoteLoadingRef.current) return;
    isQuoteLoadingRef.current = true;
    try {
      const q = await getMoexQuote(src);
      setQuote(q);
      setLastQuoteTime(new Date());
      setQuoteError(null);
    } catch {
      setQuoteError("Quote unavailable");
    } finally {
      isQuoteLoadingRef.current = false;
      setQuoteLoaded(true);
    }
  }

  async function doWorkspaceLoad(
    src?: InstrumentSource,
    tf?: Timeframe,
    start?: string,
    end?: string,
  ) {
    if (isWorkspaceLoadingRef.current) return;
    isWorkspaceLoadingRef.current = true;
    setIsUpdating(true);
    setLoadError(null);

    const useSrc = src ?? sourceRef.current;
    const useTf = tf ?? timeframeRef.current;
    const useStart = start ?? startDateRef.current;
    const useEnd = end ?? endDateRef.current;

    try {
      const ws = await loadWorkspace({
        ticker: useSrc.ticker,
        engine: useSrc.engine,
        market: useSrc.market,
        board: useSrc.board,
        timeframe: useTf,
        start: useStart,
        end: useEnd,
        calculate_indicators: true,
      });

      setLastWorkspace(ws);
      setLastPrice(ws.last_price);
      const id = ws.instrument.id;
      setLastCandleSyncTime(new Date());

      const [nextCandles, ...indicatorRows] = await Promise.all([
        getCandles(id, useTf),
        ...INDICATOR_NAMES.map((name) => getIndicatorValues(id, name, useTf)),
      ]);

      setCandles(nextCandles);
      setCandleCount(nextCandles.length);
      setIndicators(
        Object.fromEntries(
          INDICATOR_NAMES.map((name, i) => [name, indicatorRows[i] ?? []]),
        ) as IndicatorMap,
      );

      await Promise.all([reloadSignals(id, useTf), reloadLevels(id, useTf)]);
    } catch (err) {
      const msg = errorMessage(err, "Failed to load workspace data.");
      setLoadError(
        msg.toLowerCase().includes("no candle") || msg.toLowerCase().includes("no data")
          ? "No candles for this timeframe/date range. Try a more recent range."
          : msg,
      );
    } finally {
      isWorkspaceLoadingRef.current = false;
      setIsUpdating(false);
    }
  }

  // ── Effects ──────────────────────────────────────────────────────────────

  useEffect(() => {
    if (initDoneRef.current) return;
    initDoneRef.current = true;
    void fetchQuote(sourceRef.current);
    if (autoModeRef.current) void doWorkspaceLoad();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!routeTicker || !initDoneRef.current) return;
    const upper = routeTicker.toUpperCase();
    if (upper === sourceRef.current.ticker) return;
    const newSrc = { ...sourceRef.current, ticker: upper };
    setSource(newSrc);
    setSearchQuery(upper);
    void fetchQuote(newSrc);
    if (autoModeRef.current) void doWorkspaceLoad(newSrc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeTicker]);

  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (searchBoxRef.current && !searchBoxRef.current.contains(e.target as Node)) {
        setSearchOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  useEffect(() => {
    if (!autoMode || quoteRefreshSeconds === null) return;
    const ms = quoteRefreshSeconds * 1000;
    const id = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      void fetchQuote(sourceRef.current);
    }, ms);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoMode, quoteRefreshSeconds]);

  useEffect(() => {
    if (!autoMode || !candleRefreshEnabled) return;
    const ms = candleRefreshMs(timeframe);
    const id = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      void doWorkspaceLoad();
    }, ms);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoMode, candleRefreshEnabled, timeframe, startDate, endDate]);

  useEffect(() => {
    if (lastWorkspace?.instrument.id != null) {
      void reloadSignals(lastWorkspace.instrument.id, timeframe);
      void reloadLevels(lastWorkspace.instrument.id, timeframe);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeframe, lastWorkspace?.instrument.id]);

  // ── Persist settings to localStorage ─────────────────────────────────────
  useEffect(() => {
    lsSet("ticker", source.ticker);
    lsSet("engine", source.engine);
    lsSet("market", source.market);
    lsSet("board", source.board);
  }, [source]);
  useEffect(() => { lsSet("timeframe", timeframe); }, [timeframe]);
  useEffect(() => { lsSet("start", startDate); }, [startDate]);
  useEffect(() => { lsSet("end", endDate); }, [endDate]);
  useEffect(() => { lsSet("autoMode", autoMode ? "true" : "false"); }, [autoMode]);
  useEffect(() => {
    lsSet("quoteRefreshSeconds", quoteRefreshSeconds === null ? "off" : String(quoteRefreshSeconds));
  }, [quoteRefreshSeconds]);
  useEffect(() => {
    lsSet("candleRefreshEnabled", candleRefreshEnabled ? "true" : "false");
  }, [candleRefreshEnabled]);

  // ── Persist overlay settings ─────────────────────────────────────────────
  useEffect(() => { lsSet("showSma",       showSma       ? "true" : "false"); }, [showSma]);
  useEffect(() => { lsSet("showEma",       showEma       ? "true" : "false"); }, [showEma]);
  useEffect(() => { lsSet("showBollinger", showBollinger ? "true" : "false"); }, [showBollinger]);
  useEffect(() => { lsSet("showLevels",    showLevels    ? "true" : "false"); }, [showLevels]);
  useEffect(() => { lsSet("showVolume",    showVolume    ? "true" : "false"); }, [showVolume]);

  // ── Debounced instrument search ──────────────────────────────────────────
  const triggerSearch = useCallback((q: string) => {
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    if (q.trim().length < 1) { setSearchResults([]); setSearchOpen(false); return; }
    searchDebounceRef.current = setTimeout(async () => {
      setSearchLoading(true);
      try {
        const results = await searchInstruments(q.trim(), 15);
        setSearchResults(results);
        setSearchOpen(results.length > 0);
      } catch {
        setSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    }, 400);
  }, []);

  // ── Event handlers ───────────────────────────────────────────────────────

  function handleSearchInput(value: string) {
    setSearchQuery(value);
    triggerSearch(value);
  }

  function handleSelectSearchResult(result: InstrumentSearchResult) {
    const newSrc: InstrumentSource = {
      ticker: result.ticker,
      engine: result.engine ?? "stock",
      market: result.market ?? "shares",
      board: result.board ?? "TQBR",
    };
    setSource(newSrc);
    setSearchQuery(result.ticker);
    setSearchOpen(false);
    setSearchResults([]);
    void fetchQuote(newSrc);
    if (autoModeRef.current) void doWorkspaceLoad(newSrc);
  }

  function handleTimeframeChange(tf: Timeframe) {
    if (tf === timeframeRef.current) return;
    setTimeframe(tf);
    const newStart = defaultStartDate(tf);
    setStartDate(newStart);
    if (autoModeRef.current) void doWorkspaceLoad(undefined, tf, newStart);
  }

  function handleLoad() {
    void doWorkspaceLoad();
  }

  function handleOverlayToggle(key: keyof ChartOverlays) {
    switch (key) {
      case "showSma":       setShowSma(v => !v);       break;
      case "showEma":       setShowEma(v => !v);       break;
      case "showBollinger": setShowBollinger(v => !v); break;
      case "showLevels":    setShowLevels(v => !v);    break;
      case "showVolume":    setShowVolume(v => !v);    break;
    }
  }

  // ── Derived display values ───────────────────────────────────────────────
  const instrument = lastWorkspace?.instrument ?? null;
  const displayPrice = quote?.last_price ?? lastPrice?.last_close ?? null;
  const displayChange = quote?.change ?? lastPrice?.change ?? null;
  const displayChangePct = quote?.change_percent ?? lastPrice?.change_percent ?? null;
  const changePositive = (displayChange ?? 0) >= 0;
  const noData = candles.length === 0;

  const overlays: ChartOverlays = { showSma, showEma, showBollinger, showLevels, showVolume };

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="instrument-page">

      {/* 1 ─ Compact instrument header */}
      <header className="instr-header">
        <div className="instr-id-block">
          <span className="instr-ticker">{instrument?.ticker ?? source.ticker}</span>
          {instrument?.name ? (
            <span className="instr-name">{instrument.name}</span>
          ) : null}
          <span className="instr-meta">
            {instrument
              ? [instrument.engine, instrument.market, instrument.board, instrument.currency]
                  .filter(Boolean).join(" · ")
              : [source.engine, source.market, source.board].join(" · ")}
          </span>
          <span className="instr-tf-badge">{timeframe}</span>
          <span className={`auto-badge ${autoMode ? "auto-badge-on" : "auto-badge-off"}`}>
            Auto {autoMode ? "ON" : "OFF"}
          </span>
        </div>

        <div className="instr-price-block">
          {displayPrice !== null ? (
            <>
              <span className="instr-last-price">{displayPrice.toFixed(2)}</span>
              <span className={`instr-change ${changePositive ? "positive" : "negative"}`}>
                {formatChange(displayChange, displayChangePct)}
              </span>
            </>
          ) : null}
        </div>
      </header>

      {/* 2 ─ Controls row */}
      <section className="chart-controls">

        <div className="ctrl-group" ref={searchBoxRef}>
          <label className="ctrl-label">Instrument</label>
          <div className="search-wrapper">
            <input
              type="text"
              className="ctrl-input-text"
              value={searchQuery}
              onChange={(e) => handleSearchInput(e.target.value)}
              onFocus={() => { if (searchResults.length > 0) setSearchOpen(true); }}
              placeholder="SBER, GAZP…"
              autoComplete="off"
            />
            {searchLoading ? <span className="search-spinner">…</span> : null}
            {searchOpen && searchResults.length > 0 ? (
              <ul className="search-dropdown">
                {searchResults.map((r) => (
                  <li
                    key={`${r.engine}-${r.market}-${r.board}-${r.ticker}`}
                    onMouseDown={() => handleSelectSearchResult(r)}
                  >
                    <span className="search-ticker">{r.ticker}</span>
                    <span className="search-name">{r.name}</span>
                    <span className="search-meta">
                      {[r.engine, r.market, r.board].filter(Boolean).join("/")}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </div>

        <div className="ctrl-group">
          <label className="ctrl-label">Source</label>
          <span className="instr-source-chip">
            {[source.engine, source.market, source.board].join("/")}
          </span>
        </div>

        <div className="ctrl-group">
          <label className="ctrl-label">Timeframe</label>
          <div className="tf-selector">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                type="button"
                className={`tf-btn ${timeframe === tf ? "tf-btn-active" : ""}`}
                onClick={() => handleTimeframeChange(tf)}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>

        <div className="ctrl-group">
          <label className="ctrl-label">From</label>
          <input
            type="date"
            className="ctrl-input-date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </div>

        <div className="ctrl-group">
          <label className="ctrl-label">To</label>
          <input
            type="date"
            className="ctrl-input-date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </div>

        <div className="ctrl-group">
          <label className="ctrl-label">Auto mode</label>
          <button
            type="button"
            className={`auto-mode-btn ${autoMode ? "auto-mode-on" : "auto-mode-off"}`}
            onClick={() => setAutoMode((v) => !v)}
          >
            {autoMode ? "ON" : "OFF"}
          </button>
        </div>

        <div className="ctrl-group">
          <label className="ctrl-label">Quote refresh</label>
          <select
            className="ctrl-select"
            value={quoteRefreshSeconds === null ? "off" : String(quoteRefreshSeconds)}
            onChange={(e) =>
              setQuoteRefreshSeconds(
                e.target.value === "off" ? null : parseInt(e.target.value, 10),
              )
            }
          >
            {QUOTE_REFRESH_OPTIONS.map((opt) => (
              <option key={opt.label} value={opt.value === null ? "off" : String(opt.value)}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        <div className="ctrl-group ctrl-checkbox-group">
          <label className="ctrl-label">Candles</label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={candleRefreshEnabled}
              onChange={(e) => setCandleRefreshEnabled(e.target.checked)}
            />
            Auto-sync
          </label>
        </div>

        <div className="ctrl-group ctrl-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={handleLoad}
            disabled={isUpdating}
          >
            {isUpdating ? "Updating…" : "Load / update"}
          </button>
        </div>
      </section>

      {/* 3 ─ Compact status bar */}
      <div className="chart-status-bar">
        {isUpdating ? <span className="status-updating">● Updating…</span> : null}
        <span className="status-item">
          Quote: {quoteRefreshSeconds === null ? "Off" : `${quoteRefreshSeconds}s`}
        </span>
        <span className="status-item">
          Candles: {candleRefreshEnabled ? "Auto" : "Off"}
        </span>
        {lastQuoteTime !== null ? (
          <span className="status-item">Q {formatTime(lastQuoteTime)}</span>
        ) : null}
        {lastCandleSyncTime !== null ? (
          <span className="status-item">C {formatTime(lastCandleSyncTime)}</span>
        ) : null}
        {candleCount > 0 ? (
          <span className="status-item">{candleCount} candles · {timeframe}</span>
        ) : null}
        {loadError !== null ? (
          <span className="status-error" title={loadError}>{loadError}</span>
        ) : null}
      </div>

      {/* 4 ─ Price chart — higher on the page */}
      <PriceChart
        candles={candles}
        timeframe={timeframe}
        indicators={{
          sma20: indicators.sma_20,
          ema20: indicators.ema_20,
          bollingerBands: indicators.bollinger_bands_20_2,
        }}
        levels={levels?.levels ?? []}
        overlays={overlays}
        onOverlayToggle={handleOverlayToggle}
        loading={isUpdating && noData}
        error={null}
      />

      {/* 5 ─ Quote summary chips */}
      <QuoteSummaryCard quote={quote} loading={!quoteLoaded} error={quoteError} />

      {/* 6 ─ Indicator panels */}
      <div className="indicator-panels">
        <IndicatorPanel
          title="RSI 14"
          values={indicators.rsi_14}
          timeframe={timeframe}
          variant="rsi"
          loading={isUpdating && noData}
          error={null}
        />
        <IndicatorPanel
          title="MACD 12 26 9"
          values={indicators.macd_12_26_9}
          timeframe={timeframe}
          variant="macd"
          loading={isUpdating && noData}
          error={null}
        />
      </div>

      {/* 7 ─ Analysis panels — 2-col on desktop */}
      <div className="analysis-panels">
        <TechnicalSignalsPanel data={signals} loading={signalsLoading} error={signalsError} />
        <TechnicalLevelsPanel data={levels} loading={levelsLoading} error={levelsError} />
      </div>
    </div>
  );
}
