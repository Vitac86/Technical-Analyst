import type {
  CandlestickData,
  HistogramData,
  LineData,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

import type { Candle } from "../../types/candle";
import type { IndicatorValue } from "../../types/indicator";

export function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function toChartTime(timestamp: string, timeframe: string): Time {
  if (timeframe === "1d" || timeframe === "1w" || timeframe === "1mo") {
    return timestamp.slice(0, 10);
  }

  const parsed = new Date(timestamp);
  return Math.floor(parsed.getTime() / 1000) as UTCTimestamp;
}

export function buildCandlestickData(
  candles: Candle[],
  timeframe: string,
): CandlestickData<Time>[] {
  const points = new Map<string, CandlestickData<Time>>();

  candles.forEach((candle) => {
    const open = toNumber(candle.open);
    const high = toNumber(candle.high);
    const low = toNumber(candle.low);
    const close = toNumber(candle.close);

    if (open === null || high === null || low === null || close === null) {
      return;
    }

    const time = toChartTime(candle.timestamp, timeframe);
    points.set(timeKey(time), { time, open, high, low, close });
  });

  return sortByTime([...points.values()]);
}

export function buildVolumeData(
  candles: Candle[],
  timeframe: string,
): HistogramData<Time>[] {
  const points = new Map<string, HistogramData<Time>>();

  candles.forEach((candle) => {
    const volume = toNumber(candle.volume);
    const open = toNumber(candle.open);
    const close = toNumber(candle.close);

    if (volume === null || open === null || close === null) {
      return;
    }

    const time = toChartTime(candle.timestamp, timeframe);
    points.set(timeKey(time), {
      time,
      value: volume,
      color: close >= open ? "rgba(49, 177, 129, 0.38)" : "rgba(235, 87, 87, 0.38)",
    });
  });

  return sortByTime([...points.values()]);
}

export function buildIndicatorLineData(
  rows: IndicatorValue[],
  timeframe: string,
  valueKey = "value",
): LineData<Time>[] {
  const points = new Map<string, LineData<Time>>();

  rows.forEach((row) => {
    const value = toNumber(row.values[valueKey]);

    if (value === null) {
      return;
    }

    const time = toChartTime(row.timestamp, timeframe);
    points.set(timeKey(time), { time, value });
  });

  return sortByTime([...points.values()]);
}

export function buildIndicatorHistogramData(
  rows: IndicatorValue[],
  timeframe: string,
  valueKey: string,
): HistogramData<Time>[] {
  const points = new Map<string, HistogramData<Time>>();

  rows.forEach((row) => {
    const value = toNumber(row.values[valueKey]);

    if (value === null) {
      return;
    }

    const time = toChartTime(row.timestamp, timeframe);
    points.set(timeKey(time), {
      time,
      value,
      color: value >= 0 ? "rgba(49, 177, 129, 0.6)" : "rgba(235, 87, 87, 0.6)",
    });
  });

  return sortByTime([...points.values()]);
}

function sortByTime<T extends { time: Time }>(points: T[]): T[] {
  return points.sort((left, right) => timeOrder(left.time) - timeOrder(right.time));
}

function timeOrder(time: Time): number {
  if (typeof time === "number") {
    return time;
  }

  if (typeof time === "string") {
    return Date.parse(time);
  }

  return Date.UTC(time.year, time.month - 1, time.day);
}

function timeKey(time: Time): string {
  if (typeof time === "number" || typeof time === "string") {
    return String(time);
  }

  return `${time.year}-${String(time.month).padStart(2, "0")}-${String(
    time.day,
  ).padStart(2, "0")}`;
}
