import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  type IChartApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { MoexCandle } from '../../api/moexDirect';

type MobilePriceChartProps = {
  candles: MoexCandle[];
  timeframe: string;
};

// Treat MOEX Moscow time as UTC — see moexDirect.ts for rationale.
function beginToTime(begin: string, isDaily: boolean): Time {
  if (isDaily) return begin.slice(0, 10);
  return Math.floor(new Date(begin.replace(' ', 'T') + 'Z').getTime() / 1000) as UTCTimestamp;
}

export function MobilePriceChart({ candles, timeframe }: MobilePriceChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const isDaily = timeframe === '1d';

  useEffect(() => {
    const container = containerRef.current;
    if (!container || candles.length === 0) return;

    const chart: IChartApi = createChart(container, {
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

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#31b181',
      downColor: '#eb5757',
      borderUpColor: '#31b181',
      borderDownColor: '#eb5757',
      wickUpColor: '#31b181',
      wickDownColor: '#eb5757',
    });
    candleSeries.setData(
      candles.map(c => ({
        time: beginToTime(c.begin, isDaily),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    );

    const hasVolume = candles.some(c => c.volume > 0);
    if (hasVolume) {
      const volSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: 'vol',
        priceFormat: { type: 'volume' },
        priceLineVisible: false,
      });
      volSeries.setData(
        candles.map(c => ({
          time: beginToTime(c.begin, isDaily),
          value: c.volume,
          color: c.close >= c.open ? 'rgba(49,177,129,0.35)' : 'rgba(235,87,87,0.35)',
        })),
      );
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } });
    }

    chart.timeScale().fitContent();

    const ro = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [candles, isDaily]);

  return <div ref={containerRef} className="mc-chart-canvas" />;
}
