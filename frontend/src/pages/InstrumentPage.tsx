import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { IndicatorPanel } from "../components/charts/IndicatorPanel";
import { PriceChart } from "../components/charts/PriceChart";
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

const REFRESH_OPTIONS = ["off", "15", "30", "60"] as const;
type RefreshInterval = (typeof REFRESH_OPTIONS)[number];

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function sixMonthsAgo(): string {
  const d = new Date();
  d.setMonth(d.getMonth() - 6);
  return d.toISOString().slice(0, 10);
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

function formatChange(change: number | null, changePct: number | null): string {
  if (change === null || changePct === null) return "—";
  const sign = change >= 0 ? "+" : "";
  return `${sign}${change.toFixed(2)} (${sign}${changePct.toFixed(2)}%)`;
}

function formatTime(d: Date | null): string {
  if (d === null) return "—";
  return d.toLocaleTimeString("ru-RU");
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function InstrumentPage() {
  const { ticker: routeTicker } = useParams<{ ticker?: string }>();

  // Source selection
  const [source, setSource] = useState<InstrumentSource>({
    ticker: routeTicker?.toUpperCase() ?? "SBER",
    engine: "stock",
    market: "shares",
    board: "TQBR",
  });

  // Search
  const [searchQuery, setSearchQuery] = useState(source.ticker);
  const [searchResults, setSearchResults] = useState<InstrumentSearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchBoxRef = useRef<HTMLDivElement>(null);

  // Workspace settings
  const [timeframe, setTimeframe] = useState<Timeframe>("1d");
  const [startDate, setStartDate] = useState(sixMonthsAgo());
  const [endDate, setEndDate] = useState(today());

  // Loaded chart data
  const [instrumentId, setInstrumentId] = useState<number | null>(null);
  const [lastWorkspace, setLastWorkspace] = useState<WorkspaceLoadResponse | null>(null);
  const [lastPrice, setLastPrice] = useState<LastPriceSummary | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [indicators, setIndicators] = useState<IndicatorMap>(createEmptyIndicators);
  const [candleCount, setCandleCount] = useState(0);
  const [indicatorRowCount, setIndicatorRowCount] = useState(0);

  // Signals
  const [signals, setSignals] = useState<TechnicalSignalResponse | null>(null);
  const [signalsLoading, setSignalsLoading] = useState(false);
  const [signalsError, setSignalsError] = useState<string | null>(null);

  // Levels
  const [levels, setLevels] = useState<TechnicalLevelsResponse | null>(null);
  const [levelsLoading, setLevelsLoading] = useState(false);
  const [levelsError, setLevelsError] = useState<string | null>(null);

  // Quote
  const [quote, setQuote] = useState<QuoteSnapshot | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [lastQuoteRefreshTime, setLastQuoteRefreshTime] = useState<Date | null>(null);
  const [lastCandleSyncTime, setLastCandleSyncTime] = useState<Date | null>(null);

  // Auto-refresh
  const [autoRefreshInterval, setAutoRefreshInterval] = useState<RefreshInterval>("off");
  const [alsoRefreshCandles, setAlsoRefreshCandles] = useState(false);

  // UI states
  const [loadLoading, setLoadLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [calcLoading, setCalcLoading] = useState(false);

  // Refs for stable closures in the auto-refresh timer
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const alsoRefreshCandlesRef = useRef(alsoRefreshCandles);
  alsoRefreshCandlesRef.current = alsoRefreshCandles;
  const isRefreshingRef = useRef(false);

  // Keep search input in sync with route ticker on first render
  useEffect(() => {
    if (routeTicker) {
      setSource((s) => ({ ...s, ticker: routeTicker.toUpperCase() }));
      setSearchQuery(routeTicker.toUpperCase());
    }
  }, [routeTicker]);

  // Close search dropdown on outside click
  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (
        searchBoxRef.current &&
        !searchBoxRef.current.contains(e.target as Node)
      ) {
        setSearchOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  // Debounced search
  const triggerSearch = useCallback((q: string) => {
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    if (q.trim().length < 1) {
      setSearchResults([]);
      setSearchOpen(false);
      return;
    }
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

  function handleSearchInput(value: string) {
    setSearchQuery(value);
    triggerSearch(value);
  }

  function handleSelectSearchResult(result: InstrumentSearchResult) {
    setSource({
      ticker: result.ticker,
      engine: result.engine ?? "stock",
      market: result.market ?? "shares",
      board: result.board ?? "TQBR",
    });
    setSearchQuery(result.ticker);
    setSearchOpen(false);
    setSearchResults([]);
  }

  async function reloadSignals(id: number, tf: Timeframe) {
    setSignalsLoading(true);
    setSignalsError(null);
    try {
      const result = await getTechnicalSignals(id, tf);
      setSignals(result);
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
      const result = await getTechnicalLevels(id, tf);
      setLevels(result);
    } catch (err) {
      setLevelsError(errorMessage(err, "Failed to load levels."));
      setLevels(null);
    } finally {
      setLevelsLoading(false);
    }
  }

  async function fetchQuote(src: InstrumentSource) {
    setQuoteLoading(true);
    setQuoteError(null);
    try {
      const q = await getMoexQuote(src);
      setQuote(q);
      setLastQuoteRefreshTime(new Date());
    } catch {
      setQuoteError("Quote unavailable");
      setQuote(null);
    } finally {
      setQuoteLoading(false);
    }
  }

  // Fetch quote whenever source changes
  useEffect(() => {
    void fetchQuote(source);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.ticker, source.engine, source.market, source.board]);

  // Load workspace data
  async function handleLoad() {
    setLoadLoading(true);
    setLoadError(null);

    try {
      const ws = await loadWorkspace({
        ticker: source.ticker,
        engine: source.engine,
        market: source.market,
        board: source.board,
        timeframe,
        start: startDate,
        end: endDate,
        calculate_indicators: true,
      });

      setLastWorkspace(ws);
      setLastPrice(ws.last_price);
      const id = ws.instrument.id;
      setInstrumentId(id);
      setLastCandleSyncTime(new Date());

      // Load candles, indicators, and signals in parallel
      const [nextCandles, ...indicatorRows] = await Promise.all([
        getCandles(id, timeframe),
        ...INDICATOR_NAMES.map((name) => getIndicatorValues(id, name, timeframe)),
      ]);

      setCandles(nextCandles);
      setCandleCount(nextCandles.length);

      const nextIndicators = Object.fromEntries(
        INDICATOR_NAMES.map((name, i) => [name, indicatorRows[i] ?? []]),
      ) as IndicatorMap;
      setIndicators(nextIndicators);
      setIndicatorRowCount(
        INDICATOR_NAMES.reduce((n, name) => n + (nextIndicators[name].length), 0),
      );

      await Promise.all([reloadSignals(id, timeframe), reloadLevels(id, timeframe)]);
    } catch (err) {
      setLoadError(errorMessage(err, "Failed to load workspace data."));
    } finally {
      setLoadLoading(false);
    }
  }

  // Recalculate indicators only (no candle sync)
  async function handleRecalculate() {
    if (instrumentId === null) return;
    setCalcLoading(true);
    setLoadError(null);

    try {
      const [nextCandles, ...indicatorRows] = await Promise.all([
        getCandles(instrumentId, timeframe),
        ...INDICATOR_NAMES.map((name) =>
          getIndicatorValues(instrumentId, name, timeframe),
        ),
      ]);

      setCandles(nextCandles);
      setCandleCount(nextCandles.length);
      const nextIndicators = Object.fromEntries(
        INDICATOR_NAMES.map((name, i) => [name, indicatorRows[i] ?? []]),
      ) as IndicatorMap;
      setIndicators(nextIndicators);
      setIndicatorRowCount(
        INDICATOR_NAMES.reduce((n, name) => n + nextIndicators[name].length, 0),
      );

      await Promise.all([
        reloadSignals(instrumentId, timeframe),
        reloadLevels(instrumentId, timeframe),
      ]);
    } catch (err) {
      setLoadError(errorMessage(err, "Failed to reload indicators."));
    } finally {
      setCalcLoading(false);
    }
  }

  // Reload signals and levels when timeframe changes and data is already loaded
  useEffect(() => {
    if (instrumentId !== null) {
      void reloadSignals(instrumentId, timeframe);
      void reloadLevels(instrumentId, timeframe);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeframe, instrumentId]);

  // Auto-refresh timer — recreates only when interval setting changes
  useEffect(() => {
    if (autoRefreshInterval === "off") return;

    const ms = parseInt(autoRefreshInterval, 10) * 1000;
    const id = setInterval(async () => {
      if (isRefreshingRef.current) return;
      isRefreshingRef.current = true;
      try {
        await fetchQuote(sourceRef.current);
        if (alsoRefreshCandlesRef.current) {
          // Full workspace reload — uses latest source/timeframe via handleLoad
          await (async () => {
            setLoadLoading(true);
            setLoadError(null);
            try {
              const src = sourceRef.current;
              const ws = await loadWorkspace({
                ticker: src.ticker,
                engine: src.engine,
                market: src.market,
                board: src.board,
                timeframe,
                start: startDate,
                end: endDate,
                calculate_indicators: true,
              });
              setLastWorkspace(ws);
              setLastPrice(ws.last_price);
              const wid = ws.instrument.id;
              setInstrumentId(wid);
              setLastCandleSyncTime(new Date());

              const [nextCandles, ...indicatorRows] = await Promise.all([
                getCandles(wid, timeframe),
                ...INDICATOR_NAMES.map((name) => getIndicatorValues(wid, name, timeframe)),
              ]);
              setCandles(nextCandles);
              setCandleCount(nextCandles.length);
              const nextIndicators = Object.fromEntries(
                INDICATOR_NAMES.map((name, i) => [name, indicatorRows[i] ?? []]),
              ) as IndicatorMap;
              setIndicators(nextIndicators);
              setIndicatorRowCount(
                INDICATOR_NAMES.reduce((n, name) => n + nextIndicators[name].length, 0),
              );
              await Promise.all([
                reloadSignals(wid, timeframe),
                reloadLevels(wid, timeframe),
              ]);
            } catch (err) {
              setLoadError(errorMessage(err, "Auto-refresh failed."));
            } finally {
              setLoadLoading(false);
            }
          })();
        }
      } finally {
        isRefreshingRef.current = false;
      }
    }, ms);

    return () => clearInterval(id);
    // timeframe/startDate/endDate included so candle refresh uses correct range
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefreshInterval, timeframe, startDate, endDate]);

  const instrument = lastWorkspace?.instrument ?? null;

  return (
    <div className="instrument-page">
      {/* ------------------------------------------------------------------ */}
      {/* Header / instrument info                                            */}
      {/* ------------------------------------------------------------------ */}
      <section className="page-heading instrument-heading">
        <div>
          <p className="eyebrow">Instrument</p>
          <h2>
            {instrument?.ticker ?? source.ticker}
            {instrument?.name ? <span>{instrument.name}</span> : null}
          </h2>
          {instrument ? (
            <p className="instrument-meta">
              {[instrument.engine, instrument.market, instrument.board, instrument.currency]
                .filter(Boolean)
                .join(" · ")}
            </p>
          ) : null}
        </div>

        {/* Last-price panel (from candle history) */}
        {lastPrice?.last_close != null ? (
          <div className="last-price-panel">
            <span className="last-price-value">{lastPrice.last_close.toFixed(2)}</span>
            <span
              className={
                "last-price-change " +
                ((lastPrice.change ?? 0) >= 0 ? "positive" : "negative")
              }
            >
              {formatChange(lastPrice.change, lastPrice.change_percent)}
            </span>
            {lastPrice.last_timestamp ? (
              <span className="last-price-ts">
                {new Date(lastPrice.last_timestamp).toLocaleDateString("ru-RU")}
              </span>
            ) : null}
          </div>
        ) : null}
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* Quote snapshot card                                                 */}
      {/* ------------------------------------------------------------------ */}
      <QuoteSummaryCard quote={quote} loading={quoteLoading} error={quoteError} />

      {/* ------------------------------------------------------------------ */}
      {/* Controls                                                            */}
      {/* ------------------------------------------------------------------ */}
      <section className="workspace-controls">
        {/* Instrument search */}
        <div className="control-group" ref={searchBoxRef}>
          <label htmlFor="instrument-search">Search instrument</label>
          <div className="search-wrapper">
            <input
              id="instrument-search"
              type="text"
              value={searchQuery}
              onChange={(e) => handleSearchInput(e.target.value)}
              onFocus={() => {
                if (searchResults.length > 0) setSearchOpen(true);
              }}
              placeholder="SBER, GAZP, USD000UTSTOM…"
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

        {/* Selected source display */}
        <div className="control-group">
          <label>Selected</label>
          <span className="selected-source">
            {source.ticker}
            {" — "}
            {[source.engine, source.market, source.board].join("/")}
          </span>
        </div>

        {/* Timeframe selector */}
        <div className="control-group">
          <label htmlFor="timeframe-select">Timeframe</label>
          <select
            id="timeframe-select"
            value={timeframe}
            onChange={(e) => setTimeframe(e.target.value as Timeframe)}
          >
            {TIMEFRAMES.map((tf) => (
              <option key={tf} value={tf}>
                {tf}
              </option>
            ))}
          </select>
        </div>

        {/* Date range */}
        <div className="control-group">
          <label htmlFor="start-date">From</label>
          <input
            id="start-date"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </div>

        <div className="control-group">
          <label htmlFor="end-date">To</label>
          <input
            id="end-date"
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </div>

        {/* Action buttons */}
        <div className="control-group control-buttons">
          <button
            type="button"
            className="btn-primary"
            onClick={() => void handleLoad()}
            disabled={loadLoading || calcLoading}
          >
            {loadLoading ? "Loading…" : "Load / update data"}
          </button>
          {instrumentId !== null ? (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void handleRecalculate()}
              disabled={loadLoading || calcLoading}
            >
              {calcLoading ? "Recalculating…" : "Recalculate indicators"}
            </button>
          ) : null}
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* Auto-refresh controls                                               */}
      {/* ------------------------------------------------------------------ */}
      <section className="workspace-controls auto-refresh-controls">
        <div className="control-group">
          <label htmlFor="auto-refresh-select">Auto-refresh</label>
          <select
            id="auto-refresh-select"
            value={autoRefreshInterval}
            onChange={(e) => setAutoRefreshInterval(e.target.value as RefreshInterval)}
          >
            <option value="off">Off</option>
            <option value="15">15 sec</option>
            <option value="30">30 sec</option>
            <option value="60">60 sec</option>
          </select>
        </div>

        {autoRefreshInterval !== "off" ? (
          <div className="control-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={alsoRefreshCandles}
                onChange={(e) => setAlsoRefreshCandles(e.target.checked)}
              />
              {" "}Also refresh candles
            </label>
          </div>
        ) : null}

        <div className="control-group refresh-status">
          <span className="refresh-stat">
            Quote refreshed: <strong>{formatTime(lastQuoteRefreshTime)}</strong>
          </span>
          {lastCandleSyncTime !== null ? (
            <span className="refresh-stat">
              Candles synced: <strong>{formatTime(lastCandleSyncTime)}</strong>
            </span>
          ) : null}
        </div>
      </section>

      {/* Error */}
      {loadError ? (
        <div className="page-alert" role="alert">
          {loadError}
        </div>
      ) : null}

      {/* Status line */}
      <p className="status-line">
        {instrument ? (
          <>
            Candles: {candleCount} · Indicator rows: {indicatorRowCount} · Timeframe:{" "}
            {timeframe}
          </>
        ) : (
          "Select an instrument and click Load / update data."
        )}
      </p>

      {/* ------------------------------------------------------------------ */}
      {/* Charts                                                              */}
      {/* ------------------------------------------------------------------ */}
      <PriceChart
        candles={candles}
        timeframe={timeframe}
        indicators={{
          sma20: indicators.sma_20,
          ema20: indicators.ema_20,
          bollingerBands: indicators.bollinger_bands_20_2,
        }}
        loading={loadLoading}
        error={loadError}
      />

      <div className="indicator-panels">
        <IndicatorPanel
          title="RSI 14"
          values={indicators.rsi_14}
          timeframe={timeframe}
          variant="rsi"
          loading={loadLoading}
          error={loadError}
        />
        <IndicatorPanel
          title="MACD 12 26 9"
          values={indicators.macd_12_26_9}
          timeframe={timeframe}
          variant="macd"
          loading={loadLoading}
          error={loadError}
        />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Technical Research Signals                                          */}
      {/* ------------------------------------------------------------------ */}
      <TechnicalSignalsPanel
        data={signals}
        loading={signalsLoading}
        error={signalsError}
      />

      {/* ------------------------------------------------------------------ */}
      {/* Levels & Targets                                                    */}
      {/* ------------------------------------------------------------------ */}
      <TechnicalLevelsPanel
        data={levels}
        loading={levelsLoading}
        error={levelsError}
      />
    </div>
  );
}
