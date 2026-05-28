import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineData,
  type LineWidth,
  type MouseEventParams,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { MoexCandle } from '../../api/moexDirect';
import {
  calculateEma,
  calculateSma,
  type IndicatorPoint,
} from '../../utils/chartIndicators';

const DEBUG_LIVE = false;
const SMA_EMA_PERIOD = 20;
const LAST_LINE_WIDTH = 1 as LineWidth;
const INDICATOR_LINE_WIDTH = 1 as LineWidth;

// Throttle interval for onNearLeftEdge callback (ms).
const NEAR_EDGE_THROTTLE_MS = 800;

// Fire onNearLeftEdge when visible logical range starts within this many bars of the left edge.
const NEAR_EDGE_THRESHOLD = 5;

type Props = {
  /** Full candle array from the initial load. Changes trigger setData + fitContent. */
  candles: MoexCandle[];
  /** Single latest candle from live polling. Triggers series.update() without zoom reset. */
  liveCandle: MoexCandle | null;
  /** Opaque key that changes when source / timeframe / preset changes. Triggers chart recreation. */
  dataKey: string;
  timeframe: string;
  showSma20: boolean;
  showEma20: boolean;
  /**
   * Called (throttled) when the user pans left near the oldest loaded candle.
   * Parent should load older candles and prepend them.
   */
  onNearLeftEdge?: () => void;
  /**
   * When `current === true`, the next candle setData is a prepend of older history.
   * The chart will save/restore the visible range instead of calling fitContent().
   * The chart resets `current` to false after consuming the signal.
   */
  prependSignalRef?: React.MutableRefObject<boolean>;
};

type OhlcSnapshot = {
  timeKey: string;
  stateKey: string;
  begin: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type IndicatorKind = 'sma' | 'ema';

const INDICATOR_STYLE: Record<IndicatorKind, { color: string; title: string }> = {
  sma: { color: '#d7b94f', title: 'SMA 20' },
  ema: { color: '#64b8d8', title: 'EMA 20' },
};

function toTime(begin: string, isDaily: boolean): Time | null {
  try {
    if (isDaily) {
      const d = begin.slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return null;
      return d as unknown as Time;
    }
    const ms = new Date(begin.replace(' ', 'T') + 'Z').getTime();
    if (!Number.isFinite(ms) || ms <= 0) return null;
    return Math.floor(ms / 1000) as UTCTimestamp;
  } catch {
    return null;
  }
}

function timeKey(time: Time): string {
  if (typeof time === 'object') {
    const month = String(time.month).padStart(2, '0');
    const day = String(time.day).padStart(2, '0');
    return `${time.year}-${month}-${day}`;
  }
  return String(time);
}

function isValidCandle(c: MoexCandle, isDaily: boolean): boolean {
  if (!c.begin) return false;
  if (toTime(c.begin, isDaily) === null) return false;
  return (
    Number.isFinite(c.open) &&
    Number.isFinite(c.high) &&
    Number.isFinite(c.low) &&
    Number.isFinite(c.close) &&
    Number.isFinite(c.volume) &&
    c.high >= c.low
  );
}

function makeSnapshot(candle: MoexCandle, isDaily: boolean): OhlcSnapshot | null {
  if (!isValidCandle(candle, isDaily)) return null;
  const time = toTime(candle.begin, isDaily);
  if (time === null) return null;

  const key = timeKey(time);
  return {
    timeKey: key,
    stateKey: [
      key,
      candle.open,
      candle.high,
      candle.low,
      candle.close,
      candle.volume,
    ].join('|'),
    begin: candle.begin,
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
    volume: candle.volume,
  };
}

function toCandleData(candles: MoexCandle[], isDaily: boolean): CandlestickData<Time>[] {
  return candles.flatMap(candle => {
    const time = toTime(candle.begin, isDaily);
    if (time === null) return [];
    return [{
      time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    }];
  });
}

function toVolumeData(candles: MoexCandle[], isDaily: boolean): HistogramData<Time>[] {
  return candles.flatMap(candle => {
    const time = toTime(candle.begin, isDaily);
    if (time === null) return [];
    return [{
      time,
      value: candle.volume,
      color: candle.close >= candle.open
        ? 'rgba(49,177,129,0.35)'
        : 'rgba(235,87,87,0.35)',
    }];
  });
}

function toIndicatorLineData(points: IndicatorPoint[], isDaily: boolean): LineData<Time>[] {
  return points.flatMap(point => {
    const time = typeof point.time === 'number'
      ? point.time as UTCTimestamp
      : toTime(point.time, isDaily);
    if (time === null || !Number.isFinite(point.value)) return [];
    return [{ time, value: point.value }];
  });
}

function formatPrice(value: number): string {
  if (!Number.isFinite(value)) return '--';
  const abs = Math.abs(value);
  const maxDigits = abs >= 1 ? 2 : 4;
  return value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: maxDigits,
  });
}

function formatVolume(value: number): string {
  if (!Number.isFinite(value)) return '--';
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1).replace(/\.0$/, '')}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, '')}K`;
  return Math.round(value).toLocaleString('en-US');
}

function formatPanelTime(begin: string, isDaily: boolean): string {
  if (isDaily) return begin.slice(0, 10);
  const hm = begin.slice(11, 16);
  return hm || begin.slice(0, 16);
}

export function MobilePriceChart({
  candles,
  liveCandle,
  dataKey,
  timeframe,
  showSma20,
  showEma20,
  onNearLeftEdge,
  prependSignalRef,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const smaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const emaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const lastPriceLineRef = useRef<IPriceLine | null>(null);
  const hasVolumeRef = useRef(false);
  const chartHasDataRef = useRef(false);
  const fullCandlesRef = useRef<MoexCandle[]>([]);
  const liveTailRef = useRef<MoexCandle[]>([]);
  const ohlcLookupRef = useRef<Map<string, OhlcSnapshot>>(new Map());
  const latestSnapshotRef = useRef<OhlcSnapshot | null>(null);
  const selectedTimeKeyRef = useRef<string | null>(null);
  const lastOhlcStateKeyRef = useRef<string | null>(null);
  const latestButtonShownRef = useRef(false);
  // Track valid candle count to compute prepended-bar count on prepend updates.
  const prevValidCountRef = useRef(0);
  // Ref mirror of onNearLeftEdge prop — avoids stale closure in chart event handler.
  const onNearLeftEdgeRef = useRef<(() => void) | undefined>(undefined);
  // Timestamp of last onNearLeftEdge call for throttle.
  const nearEdgeThrottleRef = useRef(0);

  const isDaily = timeframe === '1d';

  const [ohlcSnapshot, setOhlcSnapshot] = useState<OhlcSnapshot | null>(null);
  const [showLatestButton, setShowLatestButton] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);

  // Keep the callback ref current without re-creating chart event handlers.
  useEffect(() => {
    onNearLeftEdgeRef.current = onNearLeftEdge;
  }, [onNearLeftEdge]);

  const publishOhlcSnapshot = useCallback((next: OhlcSnapshot | null) => {
    const nextKey = next?.stateKey ?? 'empty';
    if (lastOhlcStateKeyRef.current === nextKey) return;
    lastOhlcStateKeyRef.current = nextKey;
    setOhlcSnapshot(next);
  }, []);

  const setLatestButtonVisible = useCallback((next: boolean) => {
    if (latestButtonShownRef.current === next) return;
    latestButtonShownRef.current = next;
    setShowLatestButton(next);
  }, []);

  const updateLatestButtonVisibility = useCallback((chart: IChartApi | null) => {
    if (!chart) {
      setLatestButtonVisible(false);
      return;
    }

    try {
      const pos = chart.timeScale().scrollPosition();
      setLatestButtonVisible(Number.isFinite(pos) && pos > 1.5);
    } catch {
      setLatestButtonVisible(false);
    }
  }, [setLatestButtonVisible]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    setChartError(null);
    setLatestButtonVisible(false);
    selectedTimeKeyRef.current = null;
    ohlcLookupRef.current = new Map();
    latestSnapshotRef.current = null;
    fullCandlesRef.current = [];
    liveTailRef.current = [];
    prevValidCountRef.current = 0;
    publishOhlcSnapshot(null);

    let chart: IChartApi;
    try {
      chart = createChart(container, {
        width: container.clientWidth || 360,
        height: container.clientHeight || 380,
        autoSize: true,
        layout: {
          background: { color: '#12161d' },
          textColor: '#c7d0dc',
          attributionLogo: false,
        },
        grid: {
          vertLines: { color: '#1e2530' },
          horzLines: { color: '#1e2530' },
        },
        rightPriceScale: { borderColor: '#344050' },
        timeScale: {
          borderColor: '#344050',
          timeVisible: !isDaily,
          secondsVisible: false,
        },
        crosshair: { mode: 1 },
      });
    } catch (e) {
      console.warn('[MobilePriceChart] createChart failed:', e);
      setChartError('Chart failed to initialize.');
      return;
    }

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#31b181',
      downColor: '#eb5757',
      borderUpColor: '#31b181',
      borderDownColor: '#eb5757',
      wickUpColor: '#31b181',
      wickDownColor: '#eb5757',
      priceLineVisible: false,
    });

    const volSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: 'vol',
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
    });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } });

    const onCrosshairMove = (param: MouseEventParams<Time>) => {
      if (!param.point || param.time === undefined) {
        selectedTimeKeyRef.current = null;
        publishOhlcSnapshot(latestSnapshotRef.current);
        return;
      }

      const snapshot = ohlcLookupRef.current.get(timeKey(param.time));
      if (!snapshot) return;

      selectedTimeKeyRef.current = snapshot.timeKey;
      publishOhlcSnapshot(snapshot);
    };

    const onVisibleLogicalRangeChange = () => {
      updateLatestButtonVisibility(chart);

      // Left-edge detection for lazy older-candle loading.
      if (!chartHasDataRef.current) return;
      const cb = onNearLeftEdgeRef.current;
      if (!cb) return;
      try {
        const range = chart.timeScale().getVisibleLogicalRange();
        if (range && range.from <= NEAR_EDGE_THRESHOLD) {
          const now = Date.now();
          if (now - nearEdgeThrottleRef.current > NEAR_EDGE_THROTTLE_MS) {
            nearEdgeThrottleRef.current = now;
            cb();
          }
        }
      } catch { /* ignore */ }
    };

    chart.subscribeCrosshairMove(onCrosshairMove);
    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange);

    const ro = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
    });
    ro.observe(container);

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volSeriesRef.current = volSeries;
    hasVolumeRef.current = false;
    chartHasDataRef.current = false;

    return () => {
      ro.disconnect();
      chart.unsubscribeCrosshairMove(onCrosshairMove);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange);
      try { chart.remove(); } catch { /* already removed */ }
      chartRef.current = null;
      candleSeriesRef.current = null;
      volSeriesRef.current = null;
      smaSeriesRef.current = null;
      emaSeriesRef.current = null;
      lastPriceLineRef.current = null;
      chartHasDataRef.current = false;
      hasVolumeRef.current = false;
    };
  }, [
    dataKey,
    isDaily,
    publishOhlcSnapshot,
    setLatestButtonVisible,
    updateLatestButtonVisibility,
  ]);

  useEffect(() => {
    if (!candleSeriesRef.current || !volSeriesRef.current) return;

    // Consume prepend signal before clearing lookup state.
    const isPrepend = prependSignalRef?.current === true;
    if (isPrepend && prependSignalRef) {
      prependSignalRef.current = false;
    }

    // Save visible logical range before setData when prepending older history.
    let savedRange: { from: number; to: number } | null = null;
    if (isPrepend) {
      try {
        const r = chartRef.current?.timeScale().getVisibleLogicalRange();
        if (r) savedRange = { from: r.from, to: r.to };
      } catch { /* ignore */ }
    }

    if (!isPrepend) {
      // Full reload — reset all state.
      fullCandlesRef.current = [];
      liveTailRef.current = [];
      ohlcLookupRef.current = new Map();
      latestSnapshotRef.current = null;
      selectedTimeKeyRef.current = null;
      prevValidCountRef.current = 0;
      publishOhlcSnapshot(null);
      clearLastPriceLine();
    }

    if (candles.length === 0) {
      if (!isPrepend) chartHasDataRef.current = false;
      setChartError(null);
      return;
    }

    const valid = candles.filter(c => isValidCandle(c, isDaily));
    if (DEBUG_LIVE) console.log(`[chart] setData: ${valid.length}/${candles.length} valid isPrepend=${isPrepend}`);

    if (valid.length === 0) {
      if (!isPrepend) chartHasDataRef.current = false;
      setChartError('No valid candles to display.');
      return;
    }

    const prependedCount = isPrepend
      ? Math.max(0, valid.length - prevValidCountRef.current)
      : 0;

    const mapped = toCandleData(valid, isDaily);
    const hasVolume = valid.some(c => c.volume > 0);
    hasVolumeRef.current = hasVolume;

    try {
      candleSeriesRef.current.setData(mapped);
      volSeriesRef.current.setData(hasVolume ? toVolumeData(valid, isDaily) : []);
      chartHasDataRef.current = true;
      fullCandlesRef.current = valid;
      prevValidCountRef.current = valid.length;
      rebuildOhlcLookup(valid);
      updateLastPriceLine(valid[valid.length - 1].close);

      if (isPrepend && savedRange !== null && prependedCount > 0) {
        // Restore the previously visible area shifted right by the prepended bar count.
        // This keeps the user looking at the same candles after older history is loaded.
        chartRef.current?.timeScale().setVisibleLogicalRange({
          from: savedRange.from + prependedCount,
          to:   savedRange.to  + prependedCount,
        });
      } else if (!isPrepend) {
        chartRef.current?.timeScale().fitContent();
      }

      publishOhlcSnapshot(latestSnapshotRef.current);
      setChartError(null);
      updateLatestButtonVisibility(chartRef.current);
    } catch (e) {
      console.warn('[MobilePriceChart] setData failed:', e);
      chartHasDataRef.current = false;
      setChartError('Chart data update failed.');
    }
  }, [candles, dataKey, isDaily, prependSignalRef, publishOhlcSnapshot, updateLatestButtonVisibility]);

  useEffect(() => {
    if (!chartHasDataRef.current) {
      removeIndicatorSeries('sma');
      removeIndicatorSeries('ema');
      return;
    }

    const workingCandles = getWorkingCandles();
    syncIndicatorSeries('sma', showSma20, buildIndicatorData('sma', workingCandles));
    syncIndicatorSeries('ema', showEma20, buildIndicatorData('ema', workingCandles));
  }, [candles, dataKey, isDaily, showSma20, showEma20]);

  useEffect(() => {
    if (!chartHasDataRef.current) return;
    if (!candleSeriesRef.current || !volSeriesRef.current || !liveCandle) return;
    if (!isValidCandle(liveCandle, isDaily)) return;

    const time = toTime(liveCandle.begin, isDaily);
    if (time === null) return;

    if (DEBUG_LIVE) console.log('[chart] live update:', liveCandle.begin, liveCandle.close);

    try {
      candleSeriesRef.current.update({
        time,
        open: liveCandle.open,
        high: liveCandle.high,
        low: liveCandle.low,
        close: liveCandle.close,
      });

      if (hasVolumeRef.current) {
        volSeriesRef.current.update({
          time,
          value: liveCandle.volume,
          color: liveCandle.close >= liveCandle.open
            ? 'rgba(49,177,129,0.35)'
            : 'rgba(235,87,87,0.35)',
        });
      }

      rememberLiveCandle(liveCandle);
      updateLastPriceLine(liveCandle.close);
      updateLiveIndicator('sma', showSma20);
      updateLiveIndicator('ema', showEma20);
      updateLatestButtonVisibility(chartRef.current);
    } catch (e) {
      // series.update() throws if the incoming time is older than the latest bar.
      if (DEBUG_LIVE) console.warn('[chart] live update ignored:', e);
    }
  }, [liveCandle, isDaily, showSma20, showEma20, updateLatestButtonVisibility]);

  function rebuildOhlcLookup(validCandles: MoexCandle[]): void {
    const lookup = new Map<string, OhlcSnapshot>();
    let latest: OhlcSnapshot | null = null;

    for (const candle of validCandles) {
      const snapshot = makeSnapshot(candle, isDaily);
      if (!snapshot) continue;
      lookup.set(snapshot.timeKey, snapshot);
      latest = snapshot;
    }

    ohlcLookupRef.current = lookup;
    latestSnapshotRef.current = latest;
  }

  function rememberLiveCandle(candle: MoexCandle): void {
    const snapshot = makeSnapshot(candle, isDaily);
    if (!snapshot) return;

    const tail = liveTailRef.current;
    const tailIndex = tail.findIndex(c => c.begin === candle.begin);
    if (tailIndex >= 0) {
      tail[tailIndex] = candle;
    } else {
      tail.push(candle);
      tail.sort((a, b) => a.begin.localeCompare(b.begin));
      if (tail.length > 300) tail.splice(0, tail.length - 300);
    }

    ohlcLookupRef.current.set(snapshot.timeKey, snapshot);
    if (!latestSnapshotRef.current || snapshot.begin >= latestSnapshotRef.current.begin) {
      latestSnapshotRef.current = snapshot;
    }

    if (
      selectedTimeKeyRef.current === null ||
      selectedTimeKeyRef.current === snapshot.timeKey
    ) {
      publishOhlcSnapshot(snapshot);
    }
  }

  function getWorkingCandles(): MoexCandle[] {
    const merged = [...fullCandlesRef.current];
    const indexByBegin = new Map<string, number>();
    merged.forEach((candle, index) => indexByBegin.set(candle.begin, index));

    for (const candle of liveTailRef.current) {
      const existingIndex = indexByBegin.get(candle.begin);
      if (existingIndex !== undefined) {
        merged[existingIndex] = candle;
      } else {
        indexByBegin.set(candle.begin, merged.length);
        merged.push(candle);
      }
    }

    return merged
      .filter(candle => isValidCandle(candle, isDaily))
      .sort((a, b) => a.begin.localeCompare(b.begin));
  }

  function buildIndicatorData(kind: IndicatorKind, sourceCandles: MoexCandle[]): LineData<Time>[] {
    const points = kind === 'sma'
      ? calculateSma(sourceCandles, SMA_EMA_PERIOD)
      : calculateEma(sourceCandles, SMA_EMA_PERIOD);
    return toIndicatorLineData(points, isDaily);
  }

  function indicatorSeriesRef(kind: IndicatorKind): typeof smaSeriesRef {
    return kind === 'sma' ? smaSeriesRef : emaSeriesRef;
  }

  function ensureIndicatorSeries(kind: IndicatorKind): ISeriesApi<'Line'> | null {
    const chart = chartRef.current;
    if (!chart) return null;

    const ref = indicatorSeriesRef(kind);
    if (ref.current) return ref.current;

    const style = INDICATOR_STYLE[kind];
    ref.current = chart.addSeries(LineSeries, {
      color: style.color,
      lineWidth: INDICATOR_LINE_WIDTH,
      priceLineVisible: false,
      lastValueVisible: false,
      title: style.title,
    });
    return ref.current;
  }

  function removeIndicatorSeries(kind: IndicatorKind): void {
    const chart = chartRef.current;
    const ref = indicatorSeriesRef(kind);
    if (chart && ref.current) {
      try {
        chart.removeSeries(ref.current as unknown as ISeriesApi<SeriesType>);
      } catch {
        // Chart may already be disposed while React is cleaning up.
      }
    }
    ref.current = null;
  }

  function syncIndicatorSeries(
    kind: IndicatorKind,
    active: boolean,
    data: LineData<Time>[],
  ): void {
    if (!active || data.length === 0) {
      removeIndicatorSeries(kind);
      return;
    }

    const series = ensureIndicatorSeries(kind);
    if (!series) return;

    try {
      series.setData(data);
    } catch (e) {
      console.warn(`[MobilePriceChart] ${kind} setData failed:`, e);
      setChartError('Indicator overlay update failed.');
    }
  }

  function updateLiveIndicator(kind: IndicatorKind, active: boolean): void {
    if (!active) return;

    const data = buildIndicatorData(kind, getWorkingCandles());
    const latest = data[data.length - 1];
    if (!latest) {
      removeIndicatorSeries(kind);
      return;
    }

    const series = indicatorSeriesRef(kind).current;
    if (!series) {
      syncIndicatorSeries(kind, true, data);
      return;
    }

    try {
      series.update(latest);
    } catch (e) {
      if (DEBUG_LIVE) console.warn(`[chart] ${kind} live update ignored:`, e);
    }
  }

  function updateLastPriceLine(price: number): void {
    const series = candleSeriesRef.current;
    if (!series || !Number.isFinite(price)) return;

    if (!lastPriceLineRef.current) {
      lastPriceLineRef.current = series.createPriceLine({
        price,
        color: 'rgba(169, 216, 255, 0.62)',
        lineWidth: LAST_LINE_WIDTH,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'Last',
      });
      return;
    }

    lastPriceLineRef.current.applyOptions({ price });
  }

  function clearLastPriceLine(): void {
    if (candleSeriesRef.current && lastPriceLineRef.current) {
      try {
        candleSeriesRef.current.removePriceLine(lastPriceLineRef.current);
      } catch {
        // Already removed with the chart.
      }
    }
    lastPriceLineRef.current = null;
  }

  function handleGoToLatest(): void {
    try {
      chartRef.current?.timeScale().scrollToRealTime();
      setLatestButtonVisible(false);
    } catch {
      // Keep the chart usable even if the time scale is unavailable.
    }
  }

  return (
    <div className="mc-chart-widget">
      <div className="mc-ohlc-strip" title={ohlcSnapshot?.begin ?? ''}>
        {ohlcSnapshot ? (
          <>
            <span className="mc-ohlc-time">{formatPanelTime(ohlcSnapshot.begin, isDaily)}</span>
            <span><span className="mc-ohlc-label">O</span> {formatPrice(ohlcSnapshot.open)}</span>
            <span><span className="mc-ohlc-label">H</span> {formatPrice(ohlcSnapshot.high)}</span>
            <span><span className="mc-ohlc-label">L</span> {formatPrice(ohlcSnapshot.low)}</span>
            <span className={ohlcSnapshot.close >= ohlcSnapshot.open ? 'mc-ohlc-up' : 'mc-ohlc-down'}>
              <span className="mc-ohlc-label">C</span> {formatPrice(ohlcSnapshot.close)}
            </span>
            <span><span className="mc-ohlc-label">V</span> {formatVolume(ohlcSnapshot.volume)}</span>
          </>
        ) : (
          <span className="mc-ohlc-empty">No candle selected</span>
        )}
      </div>
      <div className="mc-chart-frame">
        <div ref={containerRef} className="mc-chart-canvas" />
        {showLatestButton ? (
          <button type="button" className="mc-latest-btn" onClick={handleGoToLatest}>
            Latest
          </button>
        ) : null}
        {chartError ? (
          <div className="mc-chart-local-error" role="alert">
            {chartError}
          </div>
        ) : null}
      </div>
    </div>
  );
}
