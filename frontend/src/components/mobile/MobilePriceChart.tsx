import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { MoexCandle } from '../../api/moexDirect';

const DEBUG_LIVE = false;

type Props = {
  /** Full candle array from the initial load — changes trigger setData + fitContent. */
  candles: MoexCandle[];
  /** Single latest candle from live polling — triggers series.update() without zoom reset. */
  liveCandle: MoexCandle | null;
  /** Opaque key that changes when source / timeframe / preset changes. Triggers chart recreation. */
  dataKey: string;
  timeframe: string;
};

/** Safe timestamp conversion — returns null on invalid input instead of NaN/throwing. */
function toTime(begin: string, isDaily: boolean): Time | null {
  try {
    if (isDaily) {
      const d = begin.slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return null;
      return d as unknown as Time;
    }
    const ms = new Date(begin.replace(' ', 'T') + 'Z').getTime();
    if (!isFinite(ms) || ms <= 0) return null;
    return Math.floor(ms / 1000) as UTCTimestamp;
  } catch {
    return null;
  }
}

function isValidCandle(c: MoexCandle, isDaily: boolean): boolean {
  if (!c.begin) return false;
  if (toTime(c.begin, isDaily) === null) return false;
  return (
    isFinite(c.open) && isFinite(c.high) && isFinite(c.low) && isFinite(c.close) &&
    c.high >= c.low
  );
}

export function MobilePriceChart({ candles, liveCandle, dataKey, timeframe }: Props) {
  const containerRef    = useRef<HTMLDivElement | null>(null);
  const chartRef        = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volSeriesRef    = useRef<ISeriesApi<'Histogram'> | null>(null);
  const hasVolumeRef    = useRef(false);
  const chartHasDataRef = useRef(false);
  const isDaily         = timeframe === '1d';

  // ── Chart instance: recreate when source/timeframe/preset changes ─────────
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

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
      return;
    }

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#31b181',
      downColor: '#eb5757',
      borderUpColor: '#31b181',
      borderDownColor: '#eb5757',
      wickUpColor: '#31b181',
      wickDownColor: '#eb5757',
    });

    const volSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: 'vol',
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
    });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } });

    const ro = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
    });
    ro.observe(container);

    chartRef.current        = chart;
    candleSeriesRef.current = candleSeries;
    volSeriesRef.current    = volSeries;
    hasVolumeRef.current    = false;
    chartHasDataRef.current = false;

    return () => {
      ro.disconnect();
      try { chart.remove(); } catch { /* already removed */ }
      chartRef.current        = null;
      candleSeriesRef.current = null;
      volSeriesRef.current    = null;
      chartHasDataRef.current = false;
    };
  }, [dataKey, isDaily]);

  // ── Full data load: setData + fitContent ──────────────────────────────────
  // candles only changes when loadCandles fires (source/tf/preset change or retry).
  // Never called by live polling — live updates go through liveCandle below.
  useEffect(() => {
    if (!candleSeriesRef.current || !volSeriesRef.current || candles.length === 0) return;

    const valid = candles.filter(c => isValidCandle(c, isDaily));
    if (DEBUG_LIVE) console.log(`[chart] setData: ${valid.length}/${candles.length} valid`);

    if (valid.length === 0) {
      // All candles invalid — don't call setData with bad data; leave chart empty.
      chartHasDataRef.current = false;
      return;
    }

    const mapped = valid.map(c => ({
      time: toTime(c.begin, isDaily)!,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    const hasVolume = valid.some(c => c.volume > 0);
    hasVolumeRef.current = hasVolume;

    try {
      candleSeriesRef.current.setData(mapped);
      volSeriesRef.current.setData(
        hasVolume
          ? valid.map(c => ({
              time: toTime(c.begin, isDaily)!,
              value: c.volume,
              color: c.close >= c.open ? 'rgba(49,177,129,0.35)' : 'rgba(235,87,87,0.35)',
            }))
          : [],
      );
      chartRef.current?.timeScale().fitContent();
      chartHasDataRef.current = true;
    } catch (e) {
      console.warn('[MobilePriceChart] setData failed:', e);
      chartHasDataRef.current = false;
    }
  }, [candles, dataKey, isDaily]);

  // ── Live candle: update() only — zoom/scroll preserved ───────────────────
  // Lightweight Charts series.update() auto-scrolls only when already at right edge.
  // If the user has panned left, the chart stays at the user's scroll position.
  useEffect(() => {
    if (!chartHasDataRef.current) return;
    if (!candleSeriesRef.current || !volSeriesRef.current || !liveCandle) return;

    const time = toTime(liveCandle.begin, isDaily);
    if (time === null) return;
    if (
      !isFinite(liveCandle.open) || !isFinite(liveCandle.high) ||
      !isFinite(liveCandle.low)  || !isFinite(liveCandle.close)
    ) return;

    if (DEBUG_LIVE) console.log('[chart] live update:', liveCandle.begin, liveCandle.close);

    try {
      candleSeriesRef.current.update({
        time,
        open:  liveCandle.open,
        high:  liveCandle.high,
        low:   liveCandle.low,
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
    } catch (e) {
      // series.update() throws if the incoming time < last bar time.
      // This is safe to ignore — the chart keeps its current state.
      if (DEBUG_LIVE) console.warn('[chart] live update ignored:', e);
    }
  }, [liveCandle, isDaily]);

  return <div ref={containerRef} className="mc-chart-canvas" />;
}
