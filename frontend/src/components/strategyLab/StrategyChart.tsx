import { useEffect, useRef } from "react";
import {
  AreaSeries,
  createChart,
  type IChartApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

export interface ChartSeries {
  data: { t: number; value: number | null }[];
  lineColor: string;
  topColor: string;
  bottomColor: string;
}

type Props = {
  title: string;
  series: ChartSeries[];
  height?: number;
  precision?: number;
  emptyMessage?: string;
};

/** A compact area chart over UTC-timestamped points (equity / drawdown). */
export function StrategyChart({
  title,
  series,
  height = 260,
  precision = 0,
  emptyMessage = "No data.",
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const hasData = series.some((s) => s.data.length > 0);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || !hasData) return;

    const chart: IChartApi = createChart(container, {
      width: container.clientWidth || 640,
      height,
      autoSize: true,
      layout: {
        background: { color: "#12161d" },
        textColor: "#c7d0dc",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "#1f2630" },
        horzLines: { color: "#1f2630" },
      },
      rightPriceScale: { borderColor: "#344050" },
      timeScale: { borderColor: "#344050", timeVisible: false },
      crosshair: { mode: 1 },
    });

    for (const s of series) {
      const points = s.data
        .filter((p) => p.value !== null && Number.isFinite(p.value))
        .map((p) => ({ time: p.t as UTCTimestamp as Time, value: p.value as number }));
      if (points.length === 0) continue;
      const area = chart.addSeries(AreaSeries, {
        lineColor: s.lineColor,
        topColor: s.topColor,
        bottomColor: s.bottomColor,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        priceFormat: { type: "price", precision, minMove: 1 / 10 ** precision },
      });
      area.setData(points);
    }

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [series, hasData, height, precision]);

  return (
    <section className="panel sl-chart-panel" aria-label={title}>
      <div className="panel-header">
        <h2>{title}</h2>
      </div>
      {hasData ? (
        <div ref={containerRef} className="sl-chart-canvas" />
      ) : (
        <div className="chart-state">{emptyMessage}</div>
      )}
    </section>
  );
}
