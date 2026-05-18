import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { IndicatorPanel } from "../components/charts/IndicatorPanel";
import { PriceChart } from "../components/charts/PriceChart";
import { TechnicalSignalsPanel } from "../components/analysis/TechnicalSignalsPanel";
import { getCandles } from "../api/candles";
import { getIndicatorValues } from "../api/indicators";
import { searchInstruments } from "../api/instruments";
import { loadWorkspace } from "../api/workspace";
import { getTechnicalSignals } from "../api/analysis";
import type { Candle } from "../types/candle";
import type { IndicatorValue } from "../types/indicator";
import type { InstrumentSearchResult } from "../types/instrument";
import type { LastPriceSummary, WorkspaceLoadResponse } from "../types/workspace";
import type { TechnicalSignalResponse } from "../types/analysis";

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

  // UI states
  const [loadLoading, setLoadLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [calcLoading, setCalcLoading] = useState(false);

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

      await reloadSignals(id, timeframe);
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

      await reloadSignals(instrumentId, timeframe);
    } catch (err) {
      setLoadError(errorMessage(err, "Failed to reload indicators."));
    } finally {
      setCalcLoading(false);
    }
  }

  // Reload signals when timeframe changes and data is already loaded
  useEffect(() => {
    if (instrumentId !== null) {
      void reloadSignals(instrumentId, timeframe);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeframe, instrumentId]);

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

        {/* Last-price panel */}
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
    </div>
  );
}
