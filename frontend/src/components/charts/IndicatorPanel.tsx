import { useEffect, useMemo, useRef } from "react";
import {
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type LineWidth,
} from "lightweight-charts";

import type { IndicatorValue } from "../../types/indicator";
import {
  buildIndicatorHistogramData,
  buildIndicatorLineData,
} from "./chartData";

type IndicatorPanelProps = {
  title: string;
  values: IndicatorValue[];
  timeframe: string;
  variant: "rsi" | "macd";
  loading?: boolean;
  error?: string | null;
};

const indicatorLineWidth = 2 as LineWidth;

export function IndicatorPanel({
  title,
  values,
  timeframe,
  variant,
  loading = false,
  error = null,
}: IndicatorPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const chartData = useMemo(() => {
    if (variant === "macd") {
      return {
        primary: buildIndicatorLineData(values, timeframe, "macd"),
        secondary: buildIndicatorLineData(values, timeframe, "signal"),
        histogram: buildIndicatorHistogramData(values, timeframe, "histogram"),
      };
    }

    return {
      primary: buildIndicatorLineData(values, timeframe),
      secondary: [],
      histogram: [],
    };
  }, [timeframe, values, variant]);

  useEffect(() => {
    const container = containerRef.current;

    if (container === null || chartData.primary.length === 0 || loading || error) {
      return;
    }

    const chart: IChartApi = createChart(container, {
      width: container.clientWidth || 640,
      height: variant === "macd" ? 220 : 190,
      autoSize: true,
      layout: {
        background: { color: "#12161d" },
        textColor: "#c7d0dc",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "#242b35" },
        horzLines: { color: "#242b35" },
      },
      rightPriceScale: {
        borderColor: "#344050",
      },
      timeScale: {
        borderColor: "#344050",
        timeVisible: timeframe !== "1d",
      },
    });

    if (variant === "macd" && chartData.histogram.length > 0) {
      const histogramSeries = chart.addSeries(HistogramSeries, {
        base: 0,
        priceLineVisible: false,
      });
      histogramSeries.setData(chartData.histogram);
    }

    const primarySeries = chart.addSeries(LineSeries, {
      color: variant === "macd" ? "#56ccf2" : "#f2c94c",
      lineWidth: indicatorLineWidth,
      priceLineVisible: false,
      title: variant === "macd" ? "MACD" : "RSI",
    });
    primarySeries.setData(chartData.primary);

    if (variant === "macd" && chartData.secondary.length > 0) {
      const signalSeries = chart.addSeries(LineSeries, {
        color: "#f2994a",
        lineWidth: indicatorLineWidth,
        priceLineVisible: false,
        title: "Signal",
      });
      signalSeries.setData(chartData.secondary);
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
  }, [chartData, error, loading, timeframe, variant]);

  const empty = !loading && !error && chartData.primary.length === 0;

  return (
    <section className="panel indicator-panel">
      <div className="panel-header">
        <h2>{title}</h2>
        <span className="panel-meta">{values.length} rows</span>
      </div>
      <div className="chart-legend" aria-label={`${title} series`}>
        {variant === "rsi" ? <span className="legend-chip legend-rsi">RSI</span> : null}
        {variant === "macd" ? (
          <>
            <span className="legend-chip legend-ema">MACD</span>
            <span className="legend-chip legend-signal">Signal</span>
            <span className="legend-chip legend-volume">Histogram</span>
          </>
        ) : null}
      </div>
      {loading ? <ChartState message="Loading indicator data..." /> : null}
      {error ? <ChartState message={error} tone="error" /> : null}
      {empty ? <ChartState message={`${title} data is not available yet.`} /> : null}
      {!loading && !error && !empty ? (
        <div ref={containerRef} className="chart-canvas indicator-canvas" />
      ) : null}
    </section>
  );
}

function ChartState({
  message,
  tone = "muted",
}: {
  message: string;
  tone?: "muted" | "error";
}) {
  return <div className={`chart-state chart-state-${tone}`}>{message}</div>;
}
