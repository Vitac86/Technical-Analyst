import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';

import { MobilePriceChart }   from '../components/mobile/MobilePriceChart';
import { AssetDrawer }        from '../components/mobile/AssetDrawer';
import { AiSignalPanel }      from '../components/mobile/AiSignalPanel';
import { SettingsModal }      from '../components/mobile/SettingsModal';
import { OrderBookPanel }     from '../components/mobile/OrderBookPanel';
import type { MoexCandle, MoexSource } from '../api/moexDirect';
import { searchMobileAssets } from '../api/mobileAssetSearch';
import type { MobileAssetSearchResult } from '../api/mobileAssetSearch';
import { getProvider }        from '../data/providerRegistry';
import type { MarketDataProviderId } from '../data/types';
import { loadWatchlist, makeAssetId, saveWatchlist } from '../utils/mobileWatchlist';
import type { WatchlistAsset } from '../utils/mobileWatchlist';
import { computeAiSignal, mergeWithLive } from '../ml/aiSignal';
import type { AiSignalResult } from '../ml/types';
import { loadBcsOlderChunk } from '../api/bcsMarketData';
import { computePaShortSignal, initPaModel, onPaModelReady } from '../ml/paShortSignal';
import type { PaShortSignalResult } from '../ml/paShortSignal';
import type { AiPanelMode } from '../components/mobile/AiSignalPanel';
import { useTranslation } from '../i18n/useTranslation';
import '../styles/mobile.css';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d'] as const;
type Timeframe = (typeof TIMEFRAMES)[number];

const DATE_PRESETS = ['1D', '3D', '1W', '1M', '3M', '6M', '1Y', '3Y'] as const;
type DatePreset = (typeof DATE_PRESETS)[number];

const DEFAULT_PRESET: Record<Timeframe, DatePreset> = {
  '5m':  '1D',
  '15m': '1W',
  '1h':  '1M',
  '4h':  '3M',
  '1d':  '6M',
};

const SAFE_DATE_PRESETS: Record<Timeframe, readonly DatePreset[]> = {
  '5m':  ['1D', '3D', '1W'],
  '15m': ['1W', '1M'],
  '1h':  ['1M', '3M'],
  '4h':  ['3M', '6M'],
  '1d':  ['6M', '1Y', '3Y'],
};

type LiveStatus = 'live' | 'paused' | 'error' | 'stale' | 'reconnecting';

type SearchPointerState = {
  key: string;
  startX: number;
  startY: number;
  dragging: boolean;
};

const MOEX_POLL_MS: Record<string,number> = {"5m":3000,"15m":5000,"1h":5000,"4h":10000};
const BCS_POLL_MS:  Record<string,number> = {"5m":5000,"15m":10000,"1h":10000,"4h":15000};
const SEARCH_DRAG_THRESHOLD_PX = 9;

const MOEX_INTERVAL_LABEL: Record<Timeframe, string> = {
  '5m':  '1m -> 5m',
  '15m': '1m -> 15m',
  '1h':  '60m',
  '4h':  '60m -> 4h',
  '1d':  '1d',
};

// ---------------------------------------------------------------------------
// Provider status
// ---------------------------------------------------------------------------

type ProviderStatus = 'moex' | 'bcs' | 'bcs-fallback';

// ---------------------------------------------------------------------------
// localStorage helpers (small UI preferences only — no candle data, no tokens)
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
    case '1D': d.setDate(d.getDate() - 1);           break;
    case '3D': d.setDate(d.getDate() - 3);           break;
    case '1W': d.setDate(d.getDate() - 7);           break;
    case '1M': d.setMonth(d.getMonth() - 1);          break;
    case '3M': d.setMonth(d.getMonth() - 3);          break;
    case '6M': d.setMonth(d.getMonth() - 6);          break;
    case '1Y': d.setFullYear(d.getFullYear() - 1);    break;
    case '3Y': d.setFullYear(d.getFullYear() - 3);    break;
  }
  return d.toISOString().slice(0, 10);
}

function formatHms(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function localYmd(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function dayDiff(fromYmd: string, toYmd: string): number {
  const [fy, fm, fd] = fromYmd.split('-').map(Number);
  const [ty, tm, td] = toYmd.split('-').map(Number);
  if (!fy || !fm || !fd || !ty || !tm || !td) return 0;
  const fromUtc = Date.UTC(fy, fm - 1, fd);
  const toUtc = Date.UTC(ty, tm - 1, td);
  return Math.floor((toUtc - fromUtc) / 86_400_000);
}

function freshnessHint(tf: Timeframe, latestBegin?: string): string | null {
  if (!latestBegin) return null;
  const latestDate = latestBegin.slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(latestDate)) return null;
  const ageDays = dayDiff(latestDate, localYmd(new Date()));
  if (ageDays <= 0) return null;
  if (tf !== '1d') return ageDays === 1 ? 'Last candle: yesterday' : `Stale: ${latestDate}`;
  return ageDays > 4 ? `Stale: ${latestDate}` : null;
}

function safePresetForTimeframe(tf: Timeframe, preset: string | null): DatePreset {
  const p = preset as DatePreset;
  return DATE_PRESETS.includes(p) && SAFE_DATE_PRESETS[tf].includes(p)
    ? p
    : DEFAULT_PRESET[tf];
}

function formatPollInterval(tf: Timeframe): string {
  const ms = MOEX_POLL_MS[tf];
  return ms ? `${ms / 1000}s` : 'off';
}

// Deduplicate a sorted candle array by begin timestamp.
function dedupeSortedCandles(candles: MoexCandle[]): MoexCandle[] {
  return candles.filter((c, i) => i === 0 || c.begin !== candles[i - 1].begin);
}

// ---------------------------------------------------------------------------
// State initializers
// ---------------------------------------------------------------------------

function initSource(): MoexSource {
  const sourceProvider = lsGet('sourceProvider') === 'bcs' ? 'bcs' : 'moex';
  const board = lsGet('board') ?? 'TQBR';
  return {
    ticker: lsGet('ticker') ?? 'SBER',
    engine: lsGet('engine') ?? 'stock',
    market: lsGet('market') ?? 'shares',
    board,
    sourceProvider,
    assetGroup: (lsGet('assetGroup') as MoexSource['assetGroup']) ?? (sourceProvider === 'bcs' ? 'goods' : 'stock'),
    classCode: lsGet('classCode') ?? board,
  };
}

function initTimeframe(): Timeframe {
  const saved = lsGet('timeframe') as Timeframe | null;
  return saved && TIMEFRAMES.includes(saved) ? saved : '1d';
}

function initPreset(tf: Timeframe): DatePreset {
  return safePresetForTimeframe(tf, lsGet('datePreset'));
}

function initLiveEnabled(): boolean {
  const v = lsGet('liveEnabled');
  return v === null || v === 'true'; // default ON
}

function initDiagnosticsOpen(): boolean {
  return lsGet('diagnosticsOpen') === 'true';
}

function initOverlayToggle(key: 'showSma20' | 'showEma20'): boolean {
  return lsGet(key) === 'true';
}

function initAiPanelToggle(): boolean {
  const v = lsGet('showAiSignalPanel');
  return v === null || v === 'true'; // default ON
}

function initProviderId(): MarketDataProviderId {
  const v = lsGet('provider');
  return v === 'bcs' ? 'bcs' : 'moex';
}

function initFallbackEnabled(): boolean {
  const v = lsGet('fallbackEnabled');
  return v === null || v === 'true'; // default ON
}

// ---------------------------------------------------------------------------
// Source display helper
// ---------------------------------------------------------------------------

function isBcsSource(src: MoexSource): boolean {
  return src.sourceProvider === 'bcs';
}

function getEffectiveProvider(
  globalProviderId: MarketDataProviderId,
  src: MoexSource,
): MarketDataProviderId {
  return isBcsSource(src) ? 'bcs' : globalProviderId;
}

function bcsAssetErrorMessage(err: unknown): string {
  const msg = err instanceof Error ? err.message : 'BCS load failed.';
  return msg.toLowerCase().includes('no bcs refresh token')
    ? 'BCS token required for this instrument'
    : msg;
}

function formatSource(src: MoexSource): string {
  if (isBcsSource(src)) return src.classCode || src.board;
  if (src.market === 'selt')  return `FX · ${src.board}`;
  if (src.market === 'forts') return `FORTS · ${src.board}`;
  if (src.market === 'bonds') return `BONDS · ${src.board}`;
  return src.board;
}

function searchResultKey(r: MobileAssetSearchResult): string {
  return `${r.sourceProvider}:${r.engine}:${r.market}:${r.board}:${r.ticker}`;
}

function resultSourceLabel(r: MobileAssetSearchResult): string {
  if (r.sourceProvider === 'bcs') return r.assetGroup === 'goods' ? 'BCS GOODS' : 'BCS';
  return 'MOEX';
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MobileChartPage() {
  const { t } = useTranslation();
  const [source,     setSource]     = useState<MoexSource>(initSource);
  const [timeframe,  setTimeframe]  = useState<Timeframe>(initTimeframe);
  const [datePreset, setDatePreset] = useState<DatePreset>(() => initPreset(initTimeframe()));

  // fullCandles: set only by loadCandles/loadOlderCandles — never touched by live polling
  const [fullCandles, setFullCandles] = useState<MoexCandle[]>([]);
  // liveCandle: set only by live polling — cleared on full reload
  const [liveCandle,  setLiveCandle]  = useState<MoexCandle | null>(null);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState<string | null>(null);

  const [liveEnabled,     setLiveEnabled]     = useState(initLiveEnabled);
  const [liveStatus,      setLiveStatus]      = useState<LiveStatus>('paused');
  const [lastRefreshTime, setLastRefreshTime] = useState<string | null>(null);
  const [lastUpdateTime,  setLastUpdateTime]  = useState<string | null>(null);

  const [searchQuery,   setSearchQuery]   = useState('');
  const [searchResults, setSearchResults] = useState<MobileAssetSearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchOpen,    setSearchOpen]    = useState(false);
  const [searchBcsTokenHint, setSearchBcsTokenHint] = useState(false);

  const [drawerOpen,      setDrawerOpen]      = useState(false);
  const [watchlist,       setWatchlist]       = useState<WatchlistAsset[]>(loadWatchlist);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(initDiagnosticsOpen);
  const [showSma20,       setShowSma20]       = useState(() => initOverlayToggle('showSma20'));
  const [showEma20,       setShowEma20]       = useState(() => initOverlayToggle('showEma20'));
  const [showAiSignalPanel, setShowAiSignalPanel] = useState(initAiPanelToggle);
  const [aiSignal,        setAiSignal]        = useState<AiSignalResult | null>(null);
  const [paSignal,        setPaSignal]        = useState<PaShortSignalResult | null>(null);
  const [aiPanelMode,     setAiPanelMode]     = useState<AiPanelMode>('pa_short');
  // Incremented once when the PA model finishes loading, to re-trigger signal recompute.
  const [paModelTick,     setPaModelTick]     = useState(0);
  const [settingsOpen,    setSettingsOpen]    = useState(false);
  const [activeTab, setActiveTab] = useState<"info"|"ai"|"depth">("info");

  // Provider settings
  const [providerId,       setProviderId]       = useState<MarketDataProviderId>(initProviderId);
  const [fallbackEnabled,  setFallbackEnabled]  = useState(initFallbackEnabled);
  const [providerStatus,   setProviderStatus]   = useState<ProviderStatus>('moex');
  const [fallbackWarning,  setFallbackWarning]  = useState<string | null>(null);

  // Lazy older-candle loading state (BCS mode only).
  const [isLoadingOlder,    setIsLoadingOlder]    = useState(false);
  const [noMoreOlderCandles, setNoMoreOlderCandles] = useState(false);
  const [olderLoadError,    setOlderLoadError]    = useState<string | null>(null);

  const debounceRef     = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aiDebounceRef   = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchBoxRef    = useRef<HTMLDivElement | null>(null);
  const searchPointerRef = useRef<SearchPointerState | null>(null);
  const loadGenRef      = useRef(0);
  const liveInFlightRef = useRef(false);
  const olderInFlightRef = useRef(false);
  // Mirrors fullCandles so the live-poll closure can read current value
  // without making fullCandles a dependency of the live-polling effect.
  const fullCandlesRef  = useRef<MoexCandle[]>([]);
  // Signal to MobilePriceChart: when true, next setData call is a prepend (preserve viewport).
  const prependSignalRef = useRef(false);

  // Opaque data key — changes when source/timeframe/preset changes.
  const dataKey = `${source.engine}:${source.market}:${source.board}:${source.ticker}:${timeframe}:${datePreset}`;

  // ── Candle loading ────────────────────────────────────────────────────────

  async function loadCandles(
    src: MoexSource,
    tf: Timeframe,
    preset: DatePreset,
    options: { preserveData?: boolean } = {},
    provId: MarketDataProviderId = providerId,
    fbEnabled: boolean = fallbackEnabled,
  ) {
    loadGenRef.current += 1;
    const gen = loadGenRef.current;
    const preserveData = options.preserveData === true;

    liveInFlightRef.current = false;
    olderInFlightRef.current = false;
    setLoading(true);
    setError(null);
    setFallbackWarning(null);
    setIsLoadingOlder(false);
    setNoMoreOlderCandles(false);
    setOlderLoadError(null);
    if (!preserveData) {
      setFullCandles([]);
      setLiveCandle(null);
      setLastRefreshTime(null);
      setLastUpdateTime(null);
    }

    const params = { source: src, timeframe: tf, from: fromDate(preset), till: today() };
    const effectiveProvider = getEffectiveProvider(provId, src);
    const bcsOnlyAsset = isBcsSource(src);
    setProviderStatus(effectiveProvider);

    try {
      let data: MoexCandle[];

      if (effectiveProvider === 'bcs') {
        try {
          data = await getProvider('bcs').loadCandles(params);
          if (gen !== loadGenRef.current) return;
          setProviderStatus('bcs');
        } catch (bcsErr) {
          if (gen !== loadGenRef.current) return;
          if (fbEnabled && !bcsOnlyAsset) {
            // Fall back to MOEX and show a compact warning
            data = await getProvider('moex').loadCandles(params);
            if (gen !== loadGenRef.current) return;
            setProviderStatus('bcs-fallback');
            const msg = bcsErr instanceof Error ? bcsErr.message : 'BCS unavailable';
            setFallbackWarning(`BCS unavailable, using MOEX · ${msg}`);
          } else {
            // No fallback — keep existing chart data and surface error
            const msg = bcsOnlyAsset
              ? bcsAssetErrorMessage(bcsErr)
              : bcsErr instanceof Error ? bcsErr.message : 'BCS load failed.';
            setError(msg);
            setLoading(false);
            return;
          }
        }
      } else {
        data = await getProvider('moex').loadCandles(params);
        if (gen !== loadGenRef.current) return;
        setProviderStatus('moex');
      }

      setFullCandles(data);
      setLiveCandle(null);
      setLastRefreshTime(formatHms(new Date()));
      setLastUpdateTime(null);
    } catch (err) {
      if (gen !== loadGenRef.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load candle data.');
    } finally {
      if (gen === loadGenRef.current) setLoading(false);
    }
  }

  // ── Lazy older-candle loading (BCS only) ─────────────────────────────────

  const loadOlderCandles = useCallback(async () => {
    if (olderInFlightRef.current) return;
    if (isLoadingOlder) return;
    if (noMoreOlderCandles) return;
    if (getEffectiveProvider(providerId, source) !== 'bcs') return;
    if (providerStatus === 'bcs-fallback') return; // data is MOEX, don't try BCS older

    const currentCandles = fullCandlesRef.current;
    if (currentCandles.length === 0) return;

    const oldest = currentCandles[0];
    if (!oldest?.begin) return;

    olderInFlightRef.current = true;
    setIsLoadingOlder(true);
    setOlderLoadError(null);

    try {
      // Parse the Moscow-time begin string back to UTC for the chunk boundary.
      // oldest.begin is "YYYY-MM-DD HH:mm:ss" in Moscow time (UTC+3).
      const moscowStr = oldest.begin.replace(' ', 'T') + '+03:00';
      const oldestDate = new Date(moscowStr);
      if (isNaN(oldestDate.getTime())) {
        setOlderLoadError('Could not load older candles');
        return;
      }

      const classCode = source.classCode || source.board || 'TQBR';
      let olderCandles: MoexCandle[];
      try {
        olderCandles = await loadBcsOlderChunk(source.ticker, classCode, timeframe, oldestDate);
      } catch (err) {
        // On 400, retry once with BCS fallback message; do not crash the chart.
        const msg = err instanceof Error ? err.message : 'Could not load older candles';
        setOlderLoadError(msg);
        return;
      }

      if (olderCandles.length === 0) {
        setNoMoreOlderCandles(true);
        return;
      }

      // Prepend older candles, sort, deduplicate.
      // Signal the chart to preserve viewport before state update.
      prependSignalRef.current = true;
      setFullCandles(prev => {
        const merged = [...olderCandles, ...prev];
        merged.sort((a, b) => (a.begin < b.begin ? -1 : a.begin > b.begin ? 1 : 0));
        return dedupeSortedCandles(merged);
      });
    } finally {
      olderInFlightRef.current = false;
      setIsLoadingOlder(false);
    }
  }, [isLoadingOlder, noMoreOlderCandles, providerId, providerStatus, source, timeframe]);

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
    lsSet('sourceProvider', source.sourceProvider ?? 'moex');
    lsSet('assetGroup', source.assetGroup ?? 'unknown');
    lsSet('classCode', source.classCode ?? source.board);
  }, [source]);
  useEffect(() => { lsSet('timeframe',        timeframe);  }, [timeframe]);
  useEffect(() => { lsSet('datePreset',        datePreset); }, [datePreset]);
  useEffect(() => { lsSet('liveEnabled',       liveEnabled       ? 'true' : 'false'); }, [liveEnabled]);
  useEffect(() => { lsSet('diagnosticsOpen',   diagnosticsOpen   ? 'true' : 'false'); }, [diagnosticsOpen]);
  useEffect(() => { lsSet('showSma20',         showSma20         ? 'true' : 'false'); }, [showSma20]);
  useEffect(() => { lsSet('showEma20',         showEma20         ? 'true' : 'false'); }, [showEma20]);
  useEffect(() => { lsSet('showAiSignalPanel', showAiSignalPanel ? 'true' : 'false'); }, [showAiSignalPanel]);
  // Non-sensitive provider setting is fine for localStorage
  useEffect(() => { lsSet('provider',       providerId);                              }, [providerId]);
  useEffect(() => { lsSet('fallbackEnabled', fallbackEnabled ? 'true' : 'false');     }, [fallbackEnabled]);

  // Keep fullCandlesRef current so the live-poll closure can check without
  // adding fullCandles to the live-effect deps (which would restart the interval).
  useEffect(() => { fullCandlesRef.current = fullCandles; }, [fullCandles]);

  // ── Live polling ──────────────────────────────────────────────────────────
  // KEY RULE: do NOT call setFullCandles here.
  // BCS live is not yet reliable; when BCS is selected, live mode is paused.
  // Only MOEX provider runs live polling.

  useEffect(() => {
    const liveDisabled = !liveEnabled || timeframe === '1d';
    if (liveDisabled) {
      setLiveStatus('paused');
      return;
    }

    let cancelled = false;
    const controller = new AbortController();

    async function pollOnce() {
      if (liveInFlightRef.current) return;
      if (document.visibilityState !== 'visible') return;
      if (fullCandlesRef.current.length === 0) return;

      liveInFlightRef.current = true;
      try {
        const effectiveProvider = getEffectiveProvider(providerId, source);
        const recent = await getProvider(effectiveProvider).loadRecentCandles(source, timeframe);
        if (cancelled) return;
        if (recent.length > 0) {
          setLiveCandle(recent[recent.length - 1]);
          setLiveStatus('live');
          setLastUpdateTime(formatHms(new Date()));
        } else if (!cancelled) {
          setLiveStatus('live');
        }
      } catch {
        if (!cancelled && !controller.signal.aborted) setLiveStatus('error');
      } finally {
        if (!cancelled) liveInFlightRef.current = false;
      }
    }

    function onVisibilityChange() {
      if (document.visibilityState === 'visible') void pollOnce();
    }

    void pollOnce();
    const effectiveProvider = getEffectiveProvider(providerId, source);
    const pollMs = (effectiveProvider==="bcs" ? BCS_POLL_MS[timeframe] : MOEX_POLL_MS[timeframe]) ?? 5000;
    const id = setInterval(() => { void pollOnce(); }, pollMs);
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      cancelled = true;
      liveInFlightRef.current = false;
      controller.abort();
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [liveEnabled, timeframe, source, datePreset, providerId]);

  // ── PA model init on mount ────────────────────────────────────────────────
  // Load the CatBoost JSON model once. When ready, trigger a re-render so the
  // signal recompute effect picks up the loaded model.

  useEffect(() => {
    onPaModelReady(() => setPaModelTick(t => t + 1));
    void initPaModel();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── AI signal recompute (debounced 300 ms) ────────────────────────────────

  useEffect(() => {
    if (aiDebounceRef.current) clearTimeout(aiDebounceRef.current);
    aiDebounceRef.current = setTimeout(() => {
      const merged = mergeWithLive(fullCandles, liveCandle);
      setAiSignal(computeAiSignal(merged, timeframe));
      setPaSignal(computePaShortSignal(merged));
    }, 300);
    return () => {
      if (aiDebounceRef.current) clearTimeout(aiDebounceRef.current);
    };
  }, [fullCandles, liveCandle, timeframe, paModelTick]);

  // ── Header search ─────────────────────────────────────────────────────────

  const triggerSearch = useCallback((q: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (q.trim().length < 2) {
      setSearchResults([]);
      setSearchOpen(false);
      setSearchBcsTokenHint(false);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setSearchLoading(true);
      try {
        const response = await searchMobileAssets(q.trim());
        setSearchResults(response.results);
        setSearchBcsTokenHint(response.bcsTokenRequired);
        setSearchOpen(response.results.length > 0 || response.bcsTokenRequired);
      } catch {
        setSearchResults([]);
        setSearchOpen(false);
        setSearchBcsTokenHint(false);
      } finally {
        setSearchLoading(false);
      }
    }, 350);
  }, []);

  function handleSearchInput(val: string) {
    setSearchQuery(val);
    triggerSearch(val);
  }

  function handleSelectResult(r: MobileAssetSearchResult) {
    const newSrc: MoexSource = {
      ticker: r.ticker,
      engine: r.engine,
      market: r.market,
      board: r.board,
      sourceProvider: r.sourceProvider,
      assetGroup: r.assetGroup,
      classCode: r.classCode ?? r.board,
      instrumentType: r.instrumentType,
      tradingCurrency: r.tradingCurrency,
    };
    setSource(newSrc);
    setSearchQuery('');
    setSearchOpen(false);
    setSearchResults([]);
    setSearchBcsTokenHint(false);
    void loadCandles(newSrc, timeframe, datePreset);
  }

  function handleSearchResultPointerDown(key: string, e: ReactPointerEvent<HTMLLIElement>) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    searchPointerRef.current = {
      key,
      startX: e.clientX,
      startY: e.clientY,
      dragging: false,
    };
  }

  function handleSearchResultPointerMove(e: ReactPointerEvent<HTMLLIElement>) {
    const state = searchPointerRef.current;
    if (!state || state.dragging) return;

    const dx = e.clientX - state.startX;
    const dy = e.clientY - state.startY;
    if (Math.hypot(dx, dy) > SEARCH_DRAG_THRESHOLD_PX) {
      state.dragging = true;
    }
  }

  function handleSearchResultPointerUp(r: MobileAssetSearchResult, key: string) {
    const state = searchPointerRef.current;
    searchPointerRef.current = null;
    if (!state || state.key !== key || state.dragging) return;
    handleSelectResult(r);
  }

  function clearSearchPointer() {
    searchPointerRef.current = null;
  }

  // ── Drawer: asset selection ───────────────────────────────────────────────

  function handleSelectFromDrawer(asset: WatchlistAsset) {
    const newSrc: MoexSource = {
      ticker: asset.ticker,
      engine: asset.engine,
      market: asset.market,
      board: asset.board,
      sourceProvider: asset.sourceProvider ?? 'moex',
      assetGroup: asset.assetGroup,
      classCode: asset.classCode ?? asset.board,
      instrumentType: asset.instrumentType,
      tradingCurrency: asset.tradingCurrency,
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
    const newPreset = safePresetForTimeframe(tf, datePreset);
    setTimeframe(tf);
    setDatePreset(newPreset);
    void loadCandles(source, tf, newPreset);
  }

  function handlePresetChange(preset: DatePreset) {
    if (preset === datePreset) return;
    if (!SAFE_DATE_PRESETS[timeframe].includes(preset)) return;
    setDatePreset(preset);
    void loadCandles(source, timeframe, preset);
  }

  function handleManualRefresh() {
    void loadCandles(source, timeframe, datePreset, { preserveData: true });
  }

  function handleRetry() {
    void loadCandles(source, timeframe, datePreset);
  }

  // ── Provider settings callbacks ───────────────────────────────────────────

  function handleProviderChange(id: MarketDataProviderId) {
    setProviderId(id);
    void loadCandles(source, timeframe, datePreset, {}, id, fallbackEnabled);
  }

  function handleFallbackChange(enabled: boolean) {
    setFallbackEnabled(enabled);
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
  const effectiveProviderId = getEffectiveProvider(providerId, source);
  const selectedId = makeAssetId(source.engine, source.market, source.board, source.ticker);
  const firstCandle = fullCandles[0] ?? null;
  const lastFullCandle = fullCandles[fullCandles.length - 1] ?? null;
  const latestChartCandle =
    liveCandle && (!lastFullCandle || liveCandle.begin >= lastFullCandle.begin)
      ? liveCandle
      : lastFullCandle;
  const chartCandleCount =
    fullCandles.length + (liveCandle && lastFullCandle && liveCandle.begin > lastFullCandle.begin ? 1 : 0);
  const staleHint = freshnessHint(timeframe, latestChartCandle?.begin);

  const effectiveLiveEnabled = liveEnabled && timeframe !== '1d';
  const liveStateText = loading
    ? t('chart.syncing')
    : !effectiveLiveEnabled
      ? t('chart.paused')
      : liveStatus === 'error'
        ? t('chart.error')
        : liveStatus === 'reconnecting'
          ? t('chart.reconnecting')
          : liveStatus === 'stale'
            ? t('chart.stale')
            : t('chart.live');
  const liveBadgeTone = loading ? 'syncing' : liveStatus;
  const compactError = error && hasData ? error : null;

  // Lazy loading is only offered in BCS mode on a live BCS provider (not fallback).
  const olderLoadEnabled = effectiveProviderId === 'bcs' && providerStatus === 'bcs' && hasData;
  const providerStatusLabel =
    providerStatus === 'moex'
      ? 'MOEX'
      : providerStatus === 'bcs'
        ? (isBcsSource(source) && providerId !== 'bcs' ? 'BCS (asset)' : 'BCS')
        : 'BCSв†’MOEX';

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="mc-page">

      {/* ── Provider settings modal ──────────────────────────────────── */}
      <SettingsModal open={settingsOpen} onClose={()=>setSettingsOpen(false)}
        providerId={providerId} onProviderChange={handleProviderChange}
        fallbackEnabled={fallbackEnabled} onFallbackChange={handleFallbackChange}
        liveEnabled={liveEnabled} onLiveEnabledChange={setLiveEnabled}
        liveStatus={liveStatus} lastLiveUpdateAt={lastUpdateTime}
        onReconnect={()=>{setLiveStatus("paused");void loadCandles(source,timeframe,datePreset,{preserveData:true});}}
        aiPanelMode={aiPanelMode} onAiPanelModeChange={setAiPanelMode}
      />

      {/* ── Asset drawer ────────────────────────────────────────────── */}
      <AssetDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        watchlist={watchlist}
        selectedId={selectedId}
        onSelect={handleSelectFromDrawer}
        onWatchlistChange={handleWatchlistChange}
        onSettingsOpen={() => { setDrawerOpen(false); setSettingsOpen(true); }}
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
          {isBcsSource(source) ? (
            <span className="mc-source-chip mc-source-chip-bcs">
              {source.assetGroup === 'goods' ? 'BCS GOODS' : 'BCS'}
            </span>
          ) : null}
        </div>
        <div className="mc-header-right">
          <span className={`mc-live-badge mc-live-badge--${liveBadgeTone}`}>
            {liveStateText}
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
            placeholder={t('chart.search.placeholder')}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="characters"
            spellCheck={false}
            onChange={e => handleSearchInput(e.target.value)}
            onFocus={() => { if (searchResults.length > 0 || searchBcsTokenHint) setSearchOpen(true); }}
          />
          {searchLoading ? <span className="mc-search-spinner">…</span> : null}
        </div>

        {searchOpen && (searchResults.length > 0 || searchBcsTokenHint) ? (
          <ul className="mc-search-dropdown">
            {searchBcsTokenHint ? (
              <li className="mc-search-hint-row" aria-disabled="true">
                {t('chart.search.bcsTokenRequired')}
              </li>
            ) : null}
            {searchResults.map(r => {
              const key = searchResultKey(r);
              return (
                <li
                  key={key}
                  onPointerDown={e => handleSearchResultPointerDown(key, e)}
                  onPointerMove={handleSearchResultPointerMove}
                  onPointerUp={() => handleSearchResultPointerUp(r, key)}
                  onPointerCancel={clearSearchPointer}
                >
                  <span className="mc-sr-ticker">{r.ticker}</span>
                  <span className="mc-sr-name">{r.name}</span>
                  <span className="mc-sr-meta">
                    <span className="mc-result-chip mc-result-chip-board">{r.board}</span>
                    <span className={`mc-result-chip ${r.sourceProvider === 'bcs' ? 'mc-result-chip-bcs' : 'mc-result-chip-moex'}`}>
                      {resultSourceLabel(r)}
                    </span>
                  </span>
                </li>
              );
            })}
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
            className="mc-chip mc-chip-refresh"
            onClick={handleManualRefresh}
            disabled={loading}
            title="Refresh chart data"
          >
            {loading ? t('chart.loading') : t('chart.refresh')}
          </button>
        </div>

        <div className="mc-chip-row" role="group" aria-label="Date range">
          {SAFE_DATE_PRESETS[timeframe].map(p => (
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

      {/* ── Chart overlays + settings gear ──────────────────────────── */}
      <div className="mc-chart-settings" role="group" aria-label="Chart overlays">
        <button
          type="button"
          className={`mc-overlay-chip${showSma20 ? ' mc-overlay-chip-active mc-overlay-chip-sma' : ''}`}
          onClick={() => setShowSma20(v => !v)}
          title={showSma20 ? 'Hide SMA 20' : 'Show SMA 20'}
        >
          SMA
        </button>
        <button
          type="button"
          className={`mc-overlay-chip${showEma20 ? ' mc-overlay-chip-active mc-overlay-chip-ema' : ''}`}
          onClick={() => setShowEma20(v => !v)}
          title={showEma20 ? 'Hide EMA 20' : 'Show EMA 20'}
        >
          EMA
        </button>
      </div>

      {/* ── Chart / states ──────────────────────────────────────────── */}
      <div className="mc-chart-area">
        {hasData ? (
          <div className="mc-chart-stack">
            <MobilePriceChart
              candles={fullCandles}
              liveCandle={liveCandle}
              dataKey={dataKey}
              timeframe={timeframe}
              showSma20={showSma20}
              showEma20={showEma20}
              onNearLeftEdge={olderLoadEnabled ? loadOlderCandles : undefined}
              prependSignalRef={prependSignalRef}
            />
            {loading ? <div className="mc-chart-sync-pill">Syncing...</div> : null}
            {compactError ? <div className="mc-chart-error-pill">{compactError}</div> : null}
            {fallbackWarning && !compactError ? (
              <div className="mc-chart-fallback-pill">{fallbackWarning}</div>
            ) : null}
            {/* Older-candle loading status (BCS mode) */}
            {olderLoadEnabled && isLoadingOlder ? (
              <div className="mc-older-loading-pill">{t('chart.olderLoading')}</div>
            ) : null}
            {olderLoadEnabled && noMoreOlderCandles && !isLoadingOlder ? (
              <div className="mc-older-none-pill">{t('chart.olderNone')}</div>
            ) : null}
            {olderLoadEnabled && olderLoadError && !isLoadingOlder ? (
              <div className="mc-older-error-pill">{olderLoadError}</div>
            ) : null}
          </div>
        ) : loading ? (
          <div className="mc-state mc-state-loading">
            <div className="mc-spinner" aria-hidden="true" />
            {t('chart.loading')} {source.ticker} · {timeframe}…
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
            {t('chart.nodata')} {source.ticker} · {timeframe} · {datePreset}
          </div>
        ) : null}
      </div>

      {/* ── Footer ──────────────────────────────────────────────────── */}
      {hasData?(
<>
  <div className="mc-bottom-status-row">
    <span>{source.ticker}</span><span className="mc-bs-sep">·</span>
    <span>{timeframe}</span><span className="mc-bs-sep">·</span>
    <span>{datePreset}</span><span className="mc-bs-sep">·</span>
    <span>{chartCandleCount} candles</span><span className="mc-bs-sep">·</span>
    <span className={"mc-footer-state mc-footer-state-"+liveBadgeTone}>{liveStateText}</span>
    <span className="mc-bs-sep">·</span>
    <span className={"mc-footer-provider mc-footer-provider-"+providerStatus}>{providerStatusLabel}</span>
  </div>
  <div className="mc-bottom-tabs" role="tablist">
    {(["info","ai","depth"] as const).map(tab=>(
      <button key={tab} type="button" role="tab" aria-selected={activeTab===tab}
        className={"mc-bottom-tab"+(activeTab===tab?" mc-bottom-tab-active":"")}
        onClick={()=>setActiveTab(tab)}>
        {tab==="info"?t('chart.tab.info'):tab==="ai"?t('chart.tab.ai'):t('chart.tab.depth')}
      </button>
    ))}
  </div>
  <div className="mc-bottom-content">
    {activeTab==="info"&&(
      <div className="mc-tab-info">
        {latestChartCandle&&(
          <div className="mc-info-ohlcv">
            <span className="mc-info-label">O</span><span className="mc-info-val">{latestChartCandle.open}</span>
            <span className="mc-info-label">H</span><span className="mc-info-val">{latestChartCandle.high}</span>
            <span className="mc-info-label">L</span><span className="mc-info-val">{latestChartCandle.low}</span>
            <span className="mc-info-label">C</span><span className="mc-info-val">{latestChartCandle.close}</span>
            {latestChartCandle.volume!=null&&(<><span className="mc-info-label">V</span><span className="mc-info-val">{latestChartCandle.volume.toLocaleString()}</span></>)}
          </div>
        )}
        <div className="mc-info-meta">
          <span>{t('chart.last')}: {latestChartCandle?.begin??"--"}</span>
          {lastUpdateTime?<span>{t('chart.live')}: {lastUpdateTime}</span>:null}
          {lastRefreshTime?<span>{t('chart.refreshTime')}: {lastRefreshTime}</span>:null}
          {staleHint?<span className="mc-footer-stale">{staleHint}</span>:null}
          {compactError?<span className="mc-footer-error">{t('chart.refreshFailed')}</span>:null}
          {fallbackWarning?<span className="mc-footer-fallback">BCS-MOEX</span>:null}
        </div>
        {diagnosticsOpen&&(
          <div className="mc-diagnostics">
            <span>{source.engine}/{source.market}/{source.board}/{source.ticker}</span>
            <span>Provider: {providerId.toUpperCase()}</span>
            <span>Effective: {effectiveProviderId.toUpperCase()}</span>
            <span>MOEX: {MOEX_INTERVAL_LABEL[timeframe]}</span>
            <span>Count: {chartCandleCount}</span>
            <span>First: {firstCandle?.begin??"--"}</span>
            <span>Last: {latestChartCandle?.begin??"--"}</span>
            <span>Full: {lastRefreshTime??"--"}</span>
            <span>Live: {lastUpdateTime??"--"}</span>
            {fallbackWarning?<span>Fallback: active</span>:null}
            {noMoreOlderCandles?<span>Older: exhausted</span>:null}
          </div>
        )}
        <button type="button"
          className={"mc-data-toggle"+(diagnosticsOpen?" mc-data-toggle-on":"")}
          onClick={()=>setDiagnosticsOpen(v=>!v)}>
          {diagnosticsOpen?t('chart.tab.dataHide'):t('chart.tab.data')}
        </button>
      </div>
    )}
    {activeTab==="ai"&&(
      <AiSignalPanel mode={aiPanelMode} onModeChange={setAiPanelMode} mockSignal={aiSignal} paSignal={paSignal}/>
    )}
    {activeTab==="depth"&&(
      <OrderBookPanel ticker={source.ticker} classCode={source.classCode ?? source.board}
        active={activeTab==="depth"&&!drawerOpen&&!settingsOpen}/>
    )}
  </div>
  {olderLoadEnabled&&isLoadingOlder?<div className="mc-older-loading-pill">{t('chart.olderLoading')}</div>:null}
  {olderLoadEnabled&&noMoreOlderCandles&&!isLoadingOlder?<div className="mc-older-none-pill">{t('chart.olderNone')}</div>:null}
  {olderLoadEnabled&&olderLoadError&&!isLoadingOlder?<div className="mc-older-error-pill">{olderLoadError}</div>:null}
</>
):null}

    </div>
  );
}
