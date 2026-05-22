import { useCallback, useEffect, useRef, useState } from 'react';

import { MobilePriceChart } from '../components/mobile/MobilePriceChart';
import { AssetDrawer }      from '../components/mobile/AssetDrawer';
import { loadMoexCandles, loadMoexRecentCandles, searchMoex } from '../api/moexDirect';
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

type LiveStatus = 'live' | 'paused' | 'error';

// Live poll interval per timeframe. 1d is disabled entirely.
const POLL_INTERVAL_MS: Record<string, number> = {
  '5m':  1000,
  '15m': 1000,
  '1h':  5000,
  '4h':  10000,
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

function formatHms(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
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

function initLiveEnabled(): boolean {
  const v = lsGet('liveEnabled');
  return v === null || v === 'true'; // default ON
}

// ---------------------------------------------------------------------------
// Source display helper
// ---------------------------------------------------------------------------

function formatSource(src: MoexSource): string {
  if (src.market === 'selt')  return `FX · ${src.board}`;
  if (src.market === 'forts') return `FORTS · ${src.board}`;
  if (src.market === 'bonds') return `BONDS · ${src.board}`;
  return src.board;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MobileChartPage() {
  const [source,     setSource]     = useState<MoexSource>(initSource);
  const [timeframe,  setTimeframe]  = useState<Timeframe>(initTimeframe);
  const [datePreset, setDatePreset] = useState<DatePreset>(() => initPreset(initTimeframe()));

  // fullCandles: set only by loadCandles — never touched by live polling
  const [fullCandles, setFullCandles] = useState<MoexCandle[]>([]);
  // liveCandle: set only by live polling — cleared on full reload
  const [liveCandle,  setLiveCandle]  = useState<MoexCandle | null>(null);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState<string | null>(null);

  const [liveEnabled,    setLiveEnabled]    = useState(initLiveEnabled);
  const [liveStatus,     setLiveStatus]     = useState<LiveStatus>('paused');
  const [lastUpdateTime, setLastUpdateTime] = useState<string | null>(null);

  const [searchQuery,   setSearchQuery]   = useState('');
  const [searchResults, setSearchResults] = useState<MoexSearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchOpen,    setSearchOpen]    = useState(false);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [watchlist,  setWatchlist]  = useState<WatchlistAsset[]>(loadWatchlist);

  const debounceRef     = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchBoxRef    = useRef<HTMLDivElement | null>(null);
  const loadGenRef      = useRef(0);
  const liveInFlightRef = useRef(false);
  // Mirrors fullCandles so the live-poll closure can read the current value
  // without making fullCandles a dependency of the live-polling effect.
  const fullCandlesRef  = useRef<MoexCandle[]>([]);

  // Opaque data key — changes when source/timeframe/preset changes.
  // MobilePriceChart uses this to decide when to recreate the chart.
  const dataKey = `${source.engine}:${source.market}:${source.board}:${source.ticker}:${timeframe}:${datePreset}`;

  // ── Candle loading ────────────────────────────────────────────────────────

  async function loadCandles(src: MoexSource, tf: Timeframe, preset: DatePreset) {
    loadGenRef.current += 1;
    const gen = loadGenRef.current;

    liveInFlightRef.current = false; // interrupt any in-flight live poll
    setLoading(true);
    setError(null);
    setFullCandles([]);
    setLiveCandle(null);

    try {
      const data = await loadMoexCandles(src, tf, fromDate(preset), today());
      if (gen !== loadGenRef.current) return;
      setFullCandles(data);
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
  useEffect(() => { lsSet('liveEnabled', liveEnabled ? 'true' : 'false'); }, [liveEnabled]);

  // Keep fullCandlesRef current so the live-poll closure can check without
  // adding fullCandles to the live-effect deps (which would restart the interval).
  useEffect(() => { fullCandlesRef.current = fullCandles; }, [fullCandles]);

  // ── Live polling ──────────────────────────────────────────────────────────
  // KEY RULE: do NOT call setFullCandles here.
  // setFullCandles triggers the candles effect in MobilePriceChart which calls
  // setData() + fitContent() — causing a chart blink and zoom reset every tick.
  // Instead, only update liveCandle; MobilePriceChart handles it via series.update().

  useEffect(() => {
    if (!liveEnabled || timeframe === '1d') {
      setLiveStatus('paused');
      return;
    }

    let cancelled = false;

    async function pollOnce() {
      if (liveInFlightRef.current) return;
      if (document.visibilityState !== 'visible') return;
      // Skip until the initial full load has completed.
      if (fullCandlesRef.current.length === 0) return;

      liveInFlightRef.current = true;
      try {
        const recent = await loadMoexRecentCandles(source, timeframe);
        if (cancelled) return;
        if (recent.length > 0) {
          // Only update liveCandle — NOT fullCandles.
          // MobilePriceChart.series.update() handles the visual refresh without
          // blinking or resetting zoom/scroll.
          setLiveCandle(recent[recent.length - 1]);
          setLiveStatus('live');
          setLastUpdateTime(formatHms(new Date()));
        } else if (!cancelled) {
          setLiveStatus('live'); // healthy poll, market just has no new data
        }
      } catch {
        if (!cancelled) setLiveStatus('error');
      } finally {
        if (!cancelled) liveInFlightRef.current = false;
      }
    }

    function onVisibilityChange() {
      if (document.visibilityState === 'visible') void pollOnce();
    }

    // Fire once immediately (returns early if initial load not done yet),
    // then on the per-timeframe interval.
    void pollOnce();
    const pollMs = POLL_INTERVAL_MS[timeframe] ?? 1000;
    const id = setInterval(() => { void pollOnce(); }, pollMs);
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      cancelled = true;
      liveInFlightRef.current = false;
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [liveEnabled, timeframe, source, datePreset]);

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

  const hasData    = fullCandles.length > 0;
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
          <span className="mc-source-chip">{formatSource(source)}</span>
        </div>
        <div className="mc-header-right">
          <span className={`mc-live-badge mc-live-badge--${liveStatus}`}>
            {liveStatus === 'live' ? 'LIVE' : liveStatus === 'error' ? 'ERR' : 'PAUSE'}
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
            placeholder="Search ticker…"
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
                <span className="mc-sr-meta">{r.board}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {/* ── Timeframe + date range chips + live toggle ──────────────── */}
      <div className="mc-controls">
        <div className="mc-chip-row" role="group" aria-label="Timeframe and live">
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
          <button
            type="button"
            className={`mc-chip mc-chip-live${liveEnabled ? ' mc-chip-live-on' : ''}`}
            onClick={() => setLiveEnabled(v => !v)}
            title={liveEnabled ? 'Pause live updates' : 'Enable live updates'}
          >
            ● LIVE
          </button>
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
          <MobilePriceChart
            candles={fullCandles}
            liveCandle={liveCandle}
            dataKey={dataKey}
            timeframe={timeframe}
          />
        )}
      </div>

      {/* ── Footer ──────────────────────────────────────────────────── */}
      {hasData && !loading ? (
        <div className="mc-footer">
          <span>{fullCandles.length} candles · {source.ticker} · {timeframe} · {datePreset}</span>
          {liveEnabled && lastUpdateTime ? (
            <span className="mc-footer-live">↻ {lastUpdateTime}</span>
          ) : null}
        </div>
      ) : null}

    </div>
  );
}
