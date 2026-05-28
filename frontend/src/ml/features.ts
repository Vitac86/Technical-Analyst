import type { MoexCandle } from '../api/moexDirect';
import type { FeatureResult, FeatureVector } from './types';

export const MIN_CANDLES_REQUIRED = 50;

function mean(arr: number[]): number {
  if (arr.length === 0) return 0;
  return arr.reduce((s, x) => s + x, 0) / arr.length;
}

function stddev(arr: number[], m?: number): number {
  if (arr.length < 2) return 0;
  const mu = m ?? mean(arr);
  const variance = arr.reduce((s, x) => s + (x - mu) ** 2, 0) / arr.length;
  return Math.sqrt(variance);
}

function calcSma(prices: number[], period: number): number[] {
  return prices.map((_, i) =>
    i < period - 1 ? NaN : mean(prices.slice(i - period + 1, i + 1))
  );
}

function calcEma(prices: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const result: number[] = [prices[0]];
  for (let i = 1; i < prices.length; i++) {
    result.push(prices[i] * k + result[i - 1] * (1 - k));
  }
  return result;
}

function safe(n: number, fallback = 0): number {
  return Number.isFinite(n) ? n : fallback;
}

export function calculateFeatures(candles: MoexCandle[]): FeatureResult {
  if (candles.length < MIN_CANDLES_REQUIRED) {
    return {
      available: false,
      reason: `Need at least ${MIN_CANDLES_REQUIRED} candles, got ${candles.length}`,
    };
  }

  // Work with last 50 candles only — no mutation of input.
  const slice = candles.slice(-MIN_CANDLES_REQUIRED);
  const n = slice.length - 1; // index of the latest candle

  const closes  = slice.map(c => c.close);
  const highs   = slice.map(c => c.high);
  const lows    = slice.map(c => c.low);
  const opens   = slice.map(c => c.open);
  const volumes = slice.map(c => c.volume);

  const close = closes[n];
  const high  = highs[n];
  const low   = lows[n];
  const open  = opens[n];

  if (!Number.isFinite(close) || close <= 0) {
    return { available: false, reason: 'Invalid latest close price' };
  }

  // ── Returns ────────────────────────────────────────────────────────────────
  const return_1  = closes[n - 1]  > 0 ? (close / closes[n - 1])  - 1 : 0;
  const return_3  = closes[n - 3]  > 0 ? (close / closes[n - 3])  - 1 : 0;
  const return_5  = closes[n - 5]  > 0 ? (close / closes[n - 5])  - 1 : 0;
  const return_10 = closes[n - 10] > 0 ? (close / closes[n - 10]) - 1 : 0;

  // ── Volatility (std of log returns) ───────────────────────────────────────
  function logRetStd(lookback: number): number {
    const logRets: number[] = [];
    for (let i = n - lookback + 1; i <= n; i++) {
      if (closes[i - 1] > 0 && closes[i] > 0) {
        logRets.push(Math.log(closes[i] / closes[i - 1]));
      }
    }
    return stddev(logRets);
  }
  const volatility_10 = logRetStd(10);
  const volatility_20 = logRetStd(20);

  // ── Candle shape ──────────────────────────────────────────────────────────
  const range = high - low;
  const body  = Math.abs(close - open);
  const candle_body_pct   = range > 0 ? body / range : 0;
  const candle_range_pct  = close > 0 ? range / close : 0;
  const upper_wick        = high - Math.max(open, close);
  const lower_wick        = Math.min(open, close) - low;
  const upper_wick_pct    = range > 0 ? upper_wick / range : 0;
  const lower_wick_pct    = range > 0 ? lower_wick / range : 0;

  // ── Volume ────────────────────────────────────────────────────────────────
  const prevVol5     = volumes.slice(n - 5, n);
  const avgVol5      = mean(prevVol5);
  const volume_change_5 = avgVol5 > 0 ? (volumes[n] / avgVol5) - 1 : 0;

  const vol20win       = volumes.slice(n - 19, n + 1);
  const meanVol20      = mean(vol20win);
  const stdVol20       = stddev(vol20win, meanVol20);
  const volume_zscore_20 = stdVol20 > 0 ? (volumes[n] - meanVol20) / stdVol20 : 0;

  // ── Moving averages ───────────────────────────────────────────────────────
  const smaArr = calcSma(closes, 20);
  const emaArr = calcEma(closes, 20);

  const sma20      = smaArr[n];
  const sma20prev  = smaArr[n - 1];
  const ema20      = emaArr[n];
  const ema20prev  = emaArr[n - 1];

  const price_vs_sma_20 = Number.isFinite(sma20) && sma20 > 0 ? (close / sma20) - 1 : 0;
  const price_vs_ema_20 = ema20 > 0 ? (close / ema20) - 1 : 0;
  const sma_20_slope    = Number.isFinite(sma20) && Number.isFinite(sma20prev) && close > 0
    ? (sma20 - sma20prev) / close
    : 0;
  const ema_20_slope    = ema20prev > 0 && close > 0 ? (ema20 - ema20prev) / close : 0;

  // ── High-low position over 20 candles ─────────────────────────────────────
  const highs20 = highs.slice(n - 19, n + 1);
  const lows20  = lows.slice(n - 19, n + 1);
  const max20   = Math.max(...highs20);
  const min20   = Math.min(...lows20);
  const high_low_position_20 = max20 > min20 ? (close - min20) / (max20 - min20) : 0.5;

  const features: FeatureVector = {
    return_1:             safe(return_1),
    return_3:             safe(return_3),
    return_5:             safe(return_5),
    return_10:            safe(return_10),
    volatility_10:        safe(volatility_10),
    volatility_20:        safe(volatility_20),
    candle_body_pct:      safe(candle_body_pct),
    candle_range_pct:     safe(candle_range_pct),
    upper_wick_pct:       safe(upper_wick_pct),
    lower_wick_pct:       safe(lower_wick_pct),
    volume_change_5:      safe(volume_change_5),
    volume_zscore_20:     safe(volume_zscore_20),
    price_vs_sma_20:      safe(price_vs_sma_20),
    price_vs_ema_20:      safe(price_vs_ema_20),
    sma_20_slope:         safe(sma_20_slope),
    ema_20_slope:         safe(ema_20_slope),
    high_low_position_20: safe(high_low_position_20, 0.5),
  };

  return { available: true, features };
}
