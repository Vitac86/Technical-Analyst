import { useCallback, useEffect, useRef, useState } from 'react';

import { MobilePriceChart } from '../components/mobile/MobilePriceChart';
import { AssetDrawer }      from '../components/mobile/AssetDrawer';
import { loadMoexCandles, searchMoex } from '../api/moexDirect';
import type { MoexCandle, MoexSearchResult, MoexSource } from '../api/moexDirect';
import { loadWatchlist, makeAssetId, saveWatchlist } from '../utils/mobileWatchlist';
import type { WatchlistAsset } from '../utils/mobileWatchlist';
import '../styles/mobile.css';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d'] as const;
type Timeframe = (typeof TIMEFRAMES)[number];

const DATE_PRESETS = ['1W', '1M', '3M', '6M', '1Y'] as const;
type DatePreset = (typeof DATE_PRESETS)[number];

const DEFAULT_PRESET: Record<Timeframe, DatePreset> = {
  '5m':  '1W',
  '15m': '1M',
  '1h':  '1M',
  '4h':  '6M',
  '1d':  '6M',
};

// ---------------------------------------------------------------------------
// localStorage helpers (small UI preferences only — no candle data)
// ---------------------------------------------------------------------------

const LS_PREFIX = 'technicalAnalyst.mobile.';

function lsGet(key: string): string | null {
  try { return localStorage.getItem(LS_PREFIX + key); } catch { return null; }
}
function lsSet(key: string, val: string): void {
  try { localStorage.setItem(LS_PREFIX + key, val); } catch { /* unavailable */ }
}

// ---------------------------------------------------------------------------
// Date helpers
// ---------------------------------------------------------------------------

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function fromDate(preset: DatePreset): string {
  const d = new Date();
  switch (preset) {
    case '1W': d.setDate(d.getDate() - 7);           break;
    case '1M': d.setMonth(d.getMonth() - 1);          break;
    case '3M': d.setMonth(d.getMonth() - 3);          break;
    case '6M': d.setMonth(d.getMonth() - 6);          break;
    case '1Y': d.setFullYear(d.getFullYear() - 1);    break;
  }
  return d.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// State initializers
// ---------------------------------------------------------------------------

function initSource(): MoexSource {
  return {
    ticker: lsGet('ticker') ?? 'SBER',
    engine: lsGet('engine') ?? 'stock',
    market: lsGet('market') ?? 'shares',
    board:  lsGet('board')  ?? 'TQBR',
  };
}

function initTimeframe(): Timeframe {
  return (lsGet('timeframe') as Timeframe | null) ?? '1d';
}

function initPreset(tf: Timeframe): DatePreset {
  return (lsGet('datePreset') as DatePreset | null) ?? DEFAULT_PRESET[tf];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MobileChartPage() {
  const [source,     setSource]     = useState<MoexSource>(initSource);
  const [timeframe,  setTimeframe]  = useState<Timeframe>(initTimeframe);
  const [datePreset, setDatePreset] = useState<DatePreset>(() => initPreset(initTimeframe()));

  const [candles,  setCandles]  = useState<MoexCandle[]>([]);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);

  const [searchQuery,   setSearchQuery]   = useState('');
  const [searchResults, setSearchResults] = useState<MoexSearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchOpen,    setSearchOpen]    = useState(false);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [watchlist,  setWatchlist]  = useState<WatchlistAsset[]>(loadWatchlist);

  const debounceRef  = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchBoxRef = useRef<HTMLDivElement | null>(null);
  const loadGenRef   = useRef(0);

  // ── Candle loading ────────────────────────────────────────────────────────

  async function loadCandles(src: MoexSource, tf: Timeframe, preset: DatePreset) {
    loadGenRef.current += 1;
    const gen = loadGenRef.current;

    setLoading(true);
    setError(null);
    setCandles([]);

    try {
      const data = await loadMoexCandles(src, tf, fromDate(preset), today());
      if (gen !== loadGenRef.current) return;
      setCandles(data);
    } catch (err) {
      if (gen !== loadGenRef.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load candle data.');
    } finally {
      if (gen === loadGenRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    void loadCandles(source, timeframe, datePreset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Persist small UI preferences ─────────────────────────────────────────

  useEffect(() => {
    lsSet('ticker', source.ticker);
    lsSet('engine', source.engine);
    lsSet('market', source.market);
    lsSet('board',  source.board);
  }, [source]);
  useEffect(() => { lsSet('timeframe',  timeframe);  }, [timeframe]);
  useEffect(() => { lsSet('datePreset', datePreset); }, [datePreset]);

  // ── Header search ─────────────────────────────────────────────────────────

  const triggerSearch = useCallback((q: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (q.trim().length < 2) {
      setSearchResults([]);
      setSearchOpen(false);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setSearchLoading(true);
      try {
        const results = await searchMoex(q.trim());
        setSearchResults(results);
        setSearchOpen(results.length > 0);
      } catch {
        setSearchResults([]);
        setSearchOpen(false);
      } finally {
        setSearchLoading(false);
      }
    }, 350);
  }, []);

  function handleSearchInput(val: string) {
    setSearchQuery(val);
    triggerSearch(val);
  }

  function handleSelectResult(r: MoexSearchResult) {
    const newSrc: MoexSource = {
      ticker: r.ticker,
      engine: r.engine,
      market: r.market,
      board:  r.board,
    };
    setSource(newSrc);
    setSearchQuery('');
    setSearchOpen(false);
    setSearchResults([]);
    void loadCandles(newSrc, timeframe, datePreset);
  }

  // ── Drawer: asset selection ───────────────────────────────────────────────

  function handleSelectFromDrawer(asset: WatchlistAsset) {
    const newSrc: MoexSource = {
      ticker: asset.ticker,
      engine: asset.engine,
      market: asset.market,
      board:  asset.board,
    };
    setSource(newSrc);
    void loadCandles(newSrc, timeframe, datePreset);
  }

  function handleWatchlistChange(list: WatchlistAsset[]) {
    setWatchlist(list);
    saveWatchlist(list);
  }

  // ── Timeframe / preset ───────────────────────────────────────────────────

  function handleTimeframeChange(tf: Timeframe) {
    if (tf === timeframe) return;
    const newPreset = DEFAULT_PRESET[tf];
    setTimeframe(tf);
    setDatePreset(newPreset);
    void loadCandles(source, tf, newPreset);
  }

  function handlePresetChange(preset: DatePreset) {
    if (preset === datePreset) return;
    setDatePreset(preset);
    void loadCandles(source, timeframe, preset);
  }

  function handleRetry() {
    void loadCandles(source, timeframe, datePreset);
  }

  // Close header search dropdown on outside tap
  useEffect(() => {
    function onPointerDown(e: PointerEvent) {
      if (searchBoxRef.current && !searchBoxRef.current.contains(e.target as Node)) {
        setSearchOpen(false);
      }
    }
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, []);

  // ── Derived ───────────────────────────────────────────────────────────────

  const hasData    = candles.length > 0;
  const noData     = !hasData && !loading && !error;
  const selectedId = makeAssetId(source.engine, source.market, source.board, source.ticker);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="mc-page">

      {/* ── Asset drawer ────────────────────────────────────────────── */}
      <AssetDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        watchlist={watchlist}
        selectedId={selectedId}
        onSelect={handleSelectFromDrawer}
        onWatchlistChange={handleWatchlistChange}
      />

      {/* ── Header ──────────────────────────────────────────────────── */}
      <header className="mc-header">
        <button
          type="button"
          className="mc-hamburger"
          onClick={() => setDrawerOpen(true)}
          aria-label="Open asset list"
        >
          <span /><span /><span />
        </button>
        <div className="mc-header-left">
          <span className="mc-ticker">{source.ticker}</span>
        </div>
        <div className="mc-header-right">
          <span className="mc-source-chip">
            {source.engine}/{source.market}/{source.board}
          </span>
          <span className="mc-tf-chip">{timeframe}</span>
        </div>
      </header>

      {/* ── Instrument search ───────────────────────────────────────── */}
      <div className="mc-search-section" ref={searchBoxRef}>
        <div className="mc-search-row">
          <input
            type="search"
            inputMode="text"
            className="mc-search-input"
            value={searchQuery}
            placeholder={`Search: ${source.ticker}`}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="characters"
            spellCheck={false}
            onChange={e => handleSearchInput(e.target.value)}
            onFocus={() => { if (searchResults.length > 0) setSearchOpen(true); }}
          />
          {searchLoading ? <span className="mc-search-spinner">…</span> : null}
        </div>

        {searchOpen && searchResults.length > 0 ? (
          <ul className="mc-search-dropdown">
            {searchResults.map(r => (
              <li
                key={`${r.engine}-${r.market}-${r.board}-${r.ticker}`}
                onPointerDown={() => handleSelectResult(r)}
              >
                <span className="mc-sr-ticker">{r.ticker}</span>
                <span className="mc-sr-name">{r.name}</span>
                <span className="mc-sr-meta">{r.engine}/{r.market}/{r.board}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {/* ── Timeframe + date range chips ────────────────────────────── */}
      <div className="mc-controls">
        <div className="mc-chip-row" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map(tf => (
            <button
              key={tf}
              type="button"
              className={`mc-chip ${timeframe === tf ? 'mc-chip-active' : ''}`}
              onClick={() => handleTimeframeChange(tf)}
            >
              {tf}
            </button>
          ))}
        </div>

        <div className="mc-chip-row" role="group" aria-label="Date range">
          {DATE_PRESETS.map(p => (
            <button
              key={p}
              type="button"
              className={`mc-chip mc-chip-sm ${datePreset === p ? 'mc-chip-active' : ''}`}
              onClick={() => handlePresetChange(p)}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* ── Chart / states ──────────────────────────────────────────── */}
      <div className="mc-chart-area">
        {loading ? (
          <div className="mc-state mc-state-loading">
            <div className="mc-spinner" aria-hidden="true" />
            Loading {source.ticker} · {timeframe}…
          </div>
        ) : error ? (
          <div className="mc-state mc-state-error" role="alert">
            <p>{error}</p>
            <button type="button" className="mc-retry-btn" onClick={handleRetry}>
              Retry
            </button>
          </div>
        ) : noData ? (
          <div className="mc-state mc-state-empty">
            No candles for {source.ticker} · {timeframe} · {datePreset}
          </div>
        ) : (
          <MobilePriceChart candles={candles} timeframe={timeframe} />
        )}
      </div>

      {/* ── Footer ──────────────────────────────────────────────────── */}
      {hasData && !loading ? (
        <div className="mc-footer">
          {candles.length} candles · {source.ticker} · {timeframe} · {datePreset}
        </div>
      ) : null}

    </div>
  );
}
