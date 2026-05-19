import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type LineWidth,
} from "lightweight-charts";

import type { Candle } from "../../types/candle";
import type { IndicatorValue } from "../../types/indicator";
import type { TechnicalLevel } from "../../types/levels";
import type { TechnicalSignalItem } from "../../types/analysis";
import {
  buildCandlestickData,
  buildIndicatorLineData,
  buildVolumeData,
} from "./chartData";

type PriceChartIndicators = {
  sma20?: IndicatorValue[];
  ema20?: IndicatorValue[];
  bollingerBands?: IndicatorValue[];
};

type PriceChartProps = {
  candles: Candle[];
  timeframe: string;
  indicators?: PriceChartIndicators;
  levels?: TechnicalLevel[];
  technicalSignals?: TechnicalSignalItem[];
  loading?: boolean;
  error?: string | null;
};

const lineWidth = 2 as LineWidth;

const LEVEL_STYLE: Record<string, { color: string; title: string }> = {
  support:     { color: "#4a9eff", title: "Support" },
  resistance:  { color: "#f2994a", title: "Resistance" },
  target_up:   { color: "#6fcf97", title: "Target ↑" },
  target_down: { color: "#eb8585", title: "Target ↓" },
  stop_zone:   { color: "#e8a26a", title: "Stop" },
};

const ANNOTATED_KINDS = new Set(["support", "resistance", "target_up", "target_down", "stop_zone"]);

export function PriceChart({
  candles,
  timeframe,
  indicators,
  levels,
  technicalSignals,
  loading = false,
  error = null,
}: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const chartData = useMemo(
    () => ({
      candles: buildCandlestickData(candles, timeframe),
      volume: buildVolumeData(candles, timeframe),
      sma20: buildIndicatorLineData(indicators?.sma20 ?? [], timeframe),
      ema20: buildIndicatorLineData(indicators?.ema20 ?? [], timeframe),
      bollingerUpper: buildIndicatorLineData(
        indicators?.bollingerBands ?? [],
        timeframe,
        "upper",
      ),
      bollingerMiddle: buildIndicatorLineData(
        indicators?.bollingerBands ?? [],
        timeframe,
        "middle",
      ),
      bollingerLower: buildIndicatorLineData(
        indicators?.bollingerBands ?? [],
        timeframe,
        "lower",
      ),
    }),
    [candles, indicators, timeframe],
  );

  useEffect(() => {
    const container = containerRef.current;

    if (container === null || chartData.candles.length === 0 || loading || error) {
      return;
    }

    const chart: IChartApi = createChart(container, {
      width: container.clientWidth || 640,
      height: 460,
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
      crosshair: {
        mode: 1,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#31b181",
      downColor: "#eb5757",
      borderUpColor: "#31b181",
      borderDownColor: "#eb5757",
      wickUpColor: "#31b181",
      wickDownColor: "#eb5757",
    });
    candleSeries.setData(chartData.candles);

    if (levels && levels.length > 0 && chartData.candles.length > 0) {
      const seen = new Set<string>();
      for (const level of levels) {
        if (level.price !== null && ANNOTATED_KINDS.has(level.kind) && !seen.has(level.kind)) {
          seen.add(level.kind);
          const style = LEVEL_STYLE[level.kind];
          candleSeries.createPriceLine({
            price: level.price,
            color: style.color,
            lineWidth: 1 as LineWidth,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: style.title,
          });
        }
      }
    }

    if (technicalSignals && technicalSignals.length > 0 && chartData.candles.length > 0) {
      const direction = pickSignalDirection(technicalSignals);
      if (direction !== null) {
        const latest = chartData.candles[chartData.candles.length - 1];
        candleSeries.setMarkers([{
          time: latest.time,
          position: direction === "buy" ? "belowBar" : "aboveBar",
          color: direction === "buy" ? "#6fcf97" : "#eb8585",
          shape: direction === "buy" ? "arrowUp" : "arrowDown",
          text: direction === "buy" ? "Buy" : "Sell",
          size: 1,
        }]);
      }
    }

    if (chartData.volume.length > 0) {
      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: "volume",
        priceFormat: { type: "volume" },
        priceLineVisible: false,
      });
      volumeSeries.setData(chartData.volume);
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      chart.priceScale("right").applyOptions({
        scaleMargins: { top: 0.08, bottom: 0.24 },
      });
    }

    addLine(chart, chartData.sma20, "#f2c94c", "SMA 20");
    addLine(chart, chartData.ema20, "#56ccf2", "EMA 20");
    addLine(chart, chartData.bollingerUpper, "#9b8cff", "BB upper", 1);
    addLine(chart, chartData.bollingerMiddle, "#b8a7ff", "BB middle", 1);
    addLine(chart, chartData.bollingerLower, "#9b8cff", "BB lower", 1);

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [chartData, error, levels, loading, technicalSignals, timeframe]);

  const empty = !loading && !error && chartData.candles.length === 0;

  return (
    <section className="panel chart-panel" aria-label="Price chart">
      <div className="panel-header">
        <h2>Price</h2>
        <span className="panel-meta">{timeframe}</span>
      </div>
      <div className="chart-legend" aria-label="Price overlays">
        <span className="legend-chip legend-sma">SMA 20</span>
        <span className="legend-chip legend-ema">EMA 20</span>
        <span className="legend-chip legend-bollinger">Bollinger Bands</span>
      </div>
      {loading ? <ChartState message="Loading price data..." /> : null}
      {error ? <ChartState message={error} tone="error" /> : null}
      {empty ? <ChartState message="No candles loaded for this instrument." /> : null}
      {!loading && !error && !empty ? (
        <div ref={containerRef} className="chart-canvas price-canvas" />
      ) : null}
    </section>
  );
}

function pickSignalDirection(signals: TechnicalSignalItem[]): "buy" | "sell" | null {
  const BUY = new Set(["buy", "strong_buy"]);
  const SELL = new Set(["sell", "strong_sell"]);
  const macd = signals.find((s) => s.indicator_name === "macd_12_26_9");
  if (macd) {
    if (BUY.has(macd.signal)) return "buy";
    if (SELL.has(macd.signal)) return "sell";
  }
  return null;
}

function addLine(
  chart: IChartApi,
  data: ReturnType<typeof buildIndicatorLineData>,
  color: string,
  title: string,
  width: LineWidth = lineWidth,
) {
  if (data.length === 0) {
    return;
  }

  const series = chart.addSeries(LineSeries, {
    color,
    lineWidth: width,
    priceLineVisible: false,
    lastValueVisible: false,
    title,
  });
  series.setData(data);
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
