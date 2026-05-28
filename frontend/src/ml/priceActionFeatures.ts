/**
 * Feature calculation for the pa_short_v0 model.
 *
 * Computes the same 52 features as the Python training pipeline:
 *   FEATURE_COLUMNS (30 technical) + PRICE_ACTION_FEATURE_COLUMNS (22 PA)
 *
 * Feature order is fixed and must exactly match the manifest's featureNames.
 *
 * Anti-leakage rules:
 *   - Fractals confirmed only after right_span future bars — no future data.
 *   - Latest candle uses only already-confirmed fractals (j <= n-1).
 *   - If any required feature is NaN/Infinity, returns unavailable.
 */

import type { MoexCandle } from '../api/moexDirect';

export const PA_MIN_CANDLES = 120;

// Ordered feature names — must match training (experiments_price_action.py line 302-305)
export const PA_FEATURE_NAMES = [
  // 30 technical features (features.py FEATURE_COLUMNS)
  'return_1', 'return_3', 'return_5', 'return_10', 'return_20',
  'volatility_10', 'volatility_20',
  'candle_body_pct', 'candle_range_pct', 'upper_wick_pct', 'lower_wick_pct',
  'volume_change_5', 'volume_zscore_20',
  'price_vs_sma_10', 'price_vs_sma_20', 'price_vs_sma_50',
  'price_vs_ema_10', 'price_vs_ema_20', 'price_vs_ema_50',
  'sma_20_slope', 'ema_20_slope',
  'rsi_14',
  'macd_line', 'macd_signal', 'macd_hist',
  'atr_14',
  'bollinger_position_20', 'bollinger_width_20',
  'high_low_position_20', 'high_low_position_50',
  // 22 price-action features (price_action.py PRICE_ACTION_FEATURE_COLUMNS)
  'higher_high', 'lower_high', 'higher_low', 'lower_low',
  'structure_trend',
  'break_of_structure_up', 'break_of_structure_down',
  'change_of_character_up', 'change_of_character_down',
  'close_above_last_fractal_high', 'close_below_last_fractal_low',
  'wick_above_last_fractal_high', 'wick_below_last_fractal_low',
  'sweep_high_reversal', 'sweep_low_reversal',
  'range_position_between_fractals', 'fractal_range_pct',
  'compression_near_fractal_range',
  'bars_since_fractal_high', 'bars_since_fractal_low',
  'distance_to_last_fractal_high_pct', 'distance_to_last_fractal_low_pct',
] as const;

export type PaFeatureResult =
  | { available: true; features: number[] }
  | { available: false; reason: string };

// ── Math helpers ──────────────────────────────────────────────────────────────

function rollingMean(arr: number[], period: number): number[] {
  const n = arr.length;
  const out = new Array<number>(n).fill(NaN);
  let sum = 0;
  for (let i = 0; i < n; i++) {
    sum += arr[i];
    if (i >= period) sum -= arr[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

// Rolling standard deviation with ddof=1 — matches pandas rolling(n).std()
function rollingStd1(arr: number[], period: number): number[] {
  const n = arr.length;
  const out = new Array<number>(n).fill(NaN);
  for (let i = period - 1; i < n; i++) {
    let s = 0, s2 = 0;
    for (let j = i - period + 1; j <= i; j++) { s += arr[j]; s2 += arr[j] * arr[j]; }
    const m = s / period;
    const v = (s2 - period * m * m) / (period - 1);
    out[i] = v > 0 ? Math.sqrt(v) : 0;
  }
  return out;
}

function rollingMax(arr: number[], period: number): number[] {
  const n = arr.length;
  const out = new Array<number>(n).fill(NaN);
  for (let i = period - 1; i < n; i++) {
    let mx = -Infinity;
    for (let j = i - period + 1; j <= i; j++) if (arr[j] > mx) mx = arr[j];
    out[i] = mx;
  }
  return out;
}

function rollingMin(arr: number[], period: number): number[] {
  const n = arr.length;
  const out = new Array<number>(n).fill(NaN);
  for (let i = period - 1; i < n; i++) {
    let mn = Infinity;
    for (let j = i - period + 1; j <= i; j++) if (arr[j] < mn) mn = arr[j];
    out[i] = mn;
  }
  return out;
}

// EWM with adjust=False, alpha = 2 / (span + 1)
function ewmSpan(arr: number[], span: number): number[] {
  const alpha = 2 / (span + 1);
  const out = new Array<number>(arr.length);
  out[0] = arr[0];
  for (let i = 1; i < arr.length; i++) {
    out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1];
  }
  return out;
}

// EWM with adjust=False, alpha = 1 / (com + 1)
function ewmCom(arr: number[], com: number): number[] {
  const alpha = 1 / (com + 1);
  const out = new Array<number>(arr.length);
  out[0] = arr[0];
  for (let i = 1; i < arr.length; i++) {
    out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1];
  }
  return out;
}

function forwardFill(arr: (number | null)[]): (number | null)[] {
  const out: (number | null)[] = [...arr];
  let last: number | null = null;
  for (let i = 0; i < out.length; i++) {
    if (out[i] !== null) last = out[i];
    else out[i] = last;
  }
  return out;
}

function safe(v: number): boolean {
  return Number.isFinite(v);
}

// ── Fractal detection ─────────────────────────────────────────────────────────

const LEFT_SPAN = 2;
const RIGHT_SPAN = 2;

/**
 * Returns array of confirmed fractal highs.
 * confirmed_high[j] = h[j - RIGHT_SPAN] if a fractal high was confirmed at bar j.
 * No leakage: confirmation at j requires bars j-RIGHT_SPAN+1..j (already past).
 */
function confirmedFractalHighs(highs: number[]): (number | null)[] {
  const n = highs.length;
  const out: (number | null)[] = new Array(n).fill(null);
  for (let i = LEFT_SPAN; i < n - RIGHT_SPAN; i++) {
    // Left: h[i] strictly greater than h[i-1] and h[i-2]
    let leftOk = true;
    for (let j = i - LEFT_SPAN; j < i; j++) {
      if (highs[j] >= highs[i]) { leftOk = false; break; }
    }
    if (!leftOk) continue;
    // Right: h[i] strictly greater than h[i+1] and h[i+2]
    let rightOk = true;
    for (let j = i + 1; j <= i + RIGHT_SPAN; j++) {
      if (highs[j] >= highs[i]) { rightOk = false; break; }
    }
    if (!rightOk) continue;
    // Confirmed at j = i + RIGHT_SPAN
    out[i + RIGHT_SPAN] = highs[i];
  }
  return out;
}

function confirmedFractalLows(lows: number[]): (number | null)[] {
  const n = lows.length;
  const out: (number | null)[] = new Array(n).fill(null);
  for (let i = LEFT_SPAN; i < n - RIGHT_SPAN; i++) {
    let leftOk = true;
    for (let j = i - LEFT_SPAN; j < i; j++) {
      if (lows[j] <= lows[i]) { leftOk = false; break; }
    }
    if (!leftOk) continue;
    let rightOk = true;
    for (let j = i + 1; j <= i + RIGHT_SPAN; j++) {
      if (lows[j] <= lows[i]) { rightOk = false; break; }
    }
    if (!rightOk) continue;
    out[i + RIGHT_SPAN] = lows[i];
  }
  return out;
}

// ── Main feature computation ──────────────────────────────────────────────────

export function calculatePaFeatures(candles: MoexCandle[]): PaFeatureResult {
  if (candles.length < PA_MIN_CANDLES) {
    return {
      available: false,
      reason: `Experimental model needs ${PA_MIN_CANDLES} candles, got ${candles.length}`,
    };
  }

  const n = candles.length;
  const closes  = candles.map(c => c.close);
  const highs   = candles.map(c => c.high);
  const lows    = candles.map(c => c.low);
  const opens   = candles.map(c => c.open);
  const volumes = candles.map(c => c.volume);
  const last = n - 1;

  const cl = closes[last];
  if (!safe(cl) || cl <= 0) {
    return { available: false, reason: 'Invalid latest close price' };
  }

  // ── Returns (%) — matching features.py: pct_change * 100 ────────────────────
  function returnPct(lag: number): number {
    const prev = closes[last - lag];
    return prev > 0 ? (cl / prev - 1) * 100 : NaN;
  }
  const return_1  = returnPct(1);
  const return_3  = returnPct(3);
  const return_5  = returnPct(5);
  const return_10 = returnPct(10);
  const return_20 = returnPct(20);

  // ── Volatility: rolling std of log returns * 100 ─────────────────────────────
  function logRetStd(lookback: number): number {
    const logRets: number[] = [];
    for (let i = last - lookback + 1; i <= last; i++) {
      if (closes[i - 1] > 0 && closes[i] > 0) logRets.push(Math.log(closes[i] / closes[i - 1]));
    }
    if (logRets.length < 2) return NaN;
    const m = logRets.reduce((s, x) => s + x, 0) / logRets.length;
    const v = logRets.reduce((s, x) => s + (x - m) ** 2, 0) / (logRets.length - 1);
    return Math.sqrt(v) * 100;
  }
  const volatility_10 = logRetStd(10);
  const volatility_20 = logRetStd(20);

  // ── Candle shape — matching features.py (multiply by 100) ────────────────────
  const hl  = highs[last] - lows[last];
  const body = Math.abs(cl - opens[last]);
  const upperBody = Math.max(opens[last], cl);
  const lowerBody = Math.min(opens[last], cl);

  const candle_range_pct = opens[last] > 0 ? (hl / opens[last]) * 100 : NaN;
  const candle_body_pct  = hl > 0 ? (body / hl) * 100 : 50.0;
  const upper_wick_pct   = hl > 0 ? ((highs[last] - upperBody) / hl) * 100 : 0.0;
  const lower_wick_pct   = hl > 0 ? ((lowerBody - lows[last]) / hl) * 100 : 0.0;

  // ── Volume ────────────────────────────────────────────────────────────────────
  // volume_change_5 = (vol / mean_of_prev_5 - 1) * 100
  let volMean5 = 0;
  for (let i = last - 5; i < last; i++) volMean5 += volumes[i];
  volMean5 /= 5;
  const volume_change_5 = volMean5 > 0 ? (volumes[last] / volMean5 - 1) * 100 : NaN;

  // volume_zscore_20 = (vol - mean20) / std20 using rolling window ending at last
  const vol20 = volumes.slice(last - 19, last + 1);
  const volM20 = vol20.reduce((s, x) => s + x, 0) / 20;
  const volS20 = Math.sqrt(vol20.reduce((s, x) => s + (x - volM20) ** 2, 0) / 19);
  const volume_zscore_20 = volS20 > 0 ? (volumes[last] - volM20) / volS20 : NaN;

  // ── SMA features ────────────────────────────────────────────────────────────
  const sma10All = rollingMean(closes, 10);
  const sma20All = rollingMean(closes, 20);
  const sma50All = rollingMean(closes, 50);

  const sma10 = sma10All[last];
  const sma20 = sma20All[last];
  const sma50 = sma50All[last];

  const price_vs_sma_10 = sma10 > 0 ? (cl / sma10 - 1) * 100 : NaN;
  const price_vs_sma_20 = sma20 > 0 ? (cl / sma20 - 1) * 100 : NaN;
  const price_vs_sma_50 = sma50 > 0 ? (cl / sma50 - 1) * 100 : NaN;

  // sma_20_slope = (sma20[last] / sma20[last-5] - 1) * 100
  const sma20prev5 = sma20All[last - 5];
  const sma_20_slope = sma20prev5 > 0 ? (sma20 / sma20prev5 - 1) * 100 : NaN;

  // ── EMA features ────────────────────────────────────────────────────────────
  const ema10All = ewmSpan(closes, 10);
  const ema20All = ewmSpan(closes, 20);
  const ema50All = ewmSpan(closes, 50);

  const ema10 = ema10All[last];
  const ema20 = ema20All[last];
  const ema50 = ema50All[last];

  const price_vs_ema_10 = ema10 > 0 ? (cl / ema10 - 1) * 100 : NaN;
  const price_vs_ema_20 = ema20 > 0 ? (cl / ema20 - 1) * 100 : NaN;
  const price_vs_ema_50 = ema50 > 0 ? (cl / ema50 - 1) * 100 : NaN;

  // ema_20_slope = (ema20[last] / ema20[last-5] - 1) * 100
  const ema20prev5 = ema20All[last - 5];
  const ema_20_slope = ema20prev5 > 0 ? (ema20 / ema20prev5 - 1) * 100 : NaN;

  // ── RSI(14) via Wilder EWM (com=13) ─────────────────────────────────────────
  // Matching Python: delta.clip(lower=0).ewm(com=13, adjust=False)
  // delta[0] = 0 (Python NaN treated as skip; with 120+ bars warmup error is negligible)
  const gains = closes.map((c, i) => i === 0 ? 0 : Math.max(c - closes[i - 1], 0));
  const losses = closes.map((c, i) => i === 0 ? 0 : Math.max(closes[i - 1] - c, 0));
  const avgGain = ewmCom(gains, 13);
  const avgLoss = ewmCom(losses, 13);
  const ag = avgGain[last];
  const al = avgLoss[last];
  const rsi_14 = al === 0 ? (ag === 0 ? 50 : 100) : 100 - 100 / (1 + ag / al);

  // ── MACD normalized by close (%) ─────────────────────────────────────────────
  const ema12All = ewmSpan(closes, 12);
  const ema26All = ewmSpan(closes, 26);
  const macdNorm = closes.map((c, i) =>
    c > 0 ? ((ema12All[i] - ema26All[i]) / c) * 100 : 0,
  );
  const signalNorm = ewmSpan(macdNorm, 9);
  const macd_line   = macdNorm[last];
  const macd_signal = signalNorm[last];
  const macd_hist   = macd_line - macd_signal;

  // ── ATR(14) normalized by close (%) ─────────────────────────────────────────
  const trueRange = closes.map((c, i) => {
    if (i === 0) return highs[0] - lows[0];
    const hl2 = highs[i] - lows[i];
    const hc  = Math.abs(highs[i] - closes[i - 1]);
    const lc2 = Math.abs(lows[i] - closes[i - 1]);
    return Math.max(hl2, hc, lc2);
  });
  const atrRaw = ewmCom(trueRange, 13);
  const atr_14 = cl > 0 ? (atrRaw[last] / cl) * 100 : NaN;

  // ── Bollinger Bands(20, 2) ────────────────────────────────────────────────────
  const std20 = rollingStd1(closes, 20)[last];
  const upperBB = sma20 + 2 * std20;
  const lowerBB = sma20 - 2 * std20;
  const bbRange = upperBB - lowerBB;
  const bollinger_position_20 = bbRange > 0 ? (cl - lowerBB) / bbRange : 0.5;
  const bollinger_width_20    = sma20 > 0 ? (bbRange / sma20) * 100 : NaN;

  // ── High-low range position ───────────────────────────────────────────────────
  const rollH20 = rollingMax(highs, 20)[last];
  const rollL20 = rollingMin(lows, 20)[last];
  const range20 = rollH20 - rollL20;
  const high_low_position_20 = range20 > 0 ? (cl - rollL20) / range20 : 0.5;

  const rollH50 = rollingMax(highs, 50)[last];
  const rollL50 = rollingMin(lows, 50)[last];
  const range50 = rollH50 - rollL50;
  const high_low_position_50 = range50 > 0 ? (cl - rollL50) / range50 : 0.5;

  // ── Fractal detection (leakage-free) ─────────────────────────────────────────
  const confHigh = confirmedFractalHighs(highs);
  const confLow  = confirmedFractalLows(lows);

  // Forward-fill: last_fractal_high[i] / last_fractal_low[i]
  const lastHighFF = forwardFill(confHigh);
  const lastLowFF  = forwardFill(confLow);

  const lhAtLast = lastHighFF[last];
  const llAtLast = lastLowFF[last];

  // bars_since_fractal_high/low
  let barsSinceHigh = NaN;
  let barsSinceLow  = NaN;
  {
    let lastConfHighBar = -1;
    let lastConfLowBar  = -1;
    for (let i = 0; i <= last; i++) {
      if (confHigh[i] !== null) lastConfHighBar = i;
      if (confLow[i]  !== null) lastConfLowBar  = i;
    }
    if (lastConfHighBar >= 0) barsSinceHigh = last - lastConfHighBar;
    if (lastConfLowBar  >= 0) barsSinceLow  = last - lastConfLowBar;
  }

  // distance_to_last_fractal_*_pct = (last_fractal - close) / close * 100
  const dist_high = (lhAtLast !== null && cl > 0) ? (lhAtLast - cl) / cl * 100 : NaN;
  const dist_low  = (llAtLast !== null && cl > 0) ? (llAtLast - cl) / cl * 100 : NaN;

  // ── prev_last_high / prev_last_low (for HH/LH/HL/LL detection) ───────────────
  // At each new confirmation event bar j, record lastHighFF[j-1] (the previous fractal level)
  const prevHighEvents: (number | null)[] = new Array(n).fill(null);
  const prevLowEvents:  (number | null)[] = new Array(n).fill(null);
  for (let j = 1; j <= last; j++) {
    if (confHigh[j] !== null) prevHighEvents[j] = lastHighFF[j - 1];
    if (confLow[j]  !== null) prevLowEvents[j]  = lastLowFF[j - 1];
  }
  const prevHighFF = forwardFill(prevHighEvents);
  const prevLowFF  = forwardFill(prevLowEvents);

  const plh = prevHighFF[last];
  const pll = prevLowFF[last];

  const bothHighsKnown = lhAtLast !== null && plh !== null;
  const bothLowsKnown  = llAtLast !== null && pll !== null;

  const higher_high = bothHighsKnown ? (lhAtLast! > plh! ? 1 : 0) : 0;
  const lower_high  = bothHighsKnown ? (lhAtLast! < plh! ? 1 : 0) : 0;
  const higher_low  = bothLowsKnown  ? (llAtLast! > pll! ? 1 : 0) : 0;
  const lower_low   = bothLowsKnown  ? (llAtLast! < pll! ? 1 : 0) : 0;

  const bullish = higher_high === 1 && higher_low === 1;
  const bearish = lower_high  === 1 && lower_low  === 1;
  const structure_trend = bullish ? 1 : bearish ? -1 : 0;

  // ── Close / wick position relative to fractal levels ─────────────────────────
  const close_above = (lhAtLast !== null && cl > lhAtLast) ? 1 : 0;
  const close_below = (llAtLast !== null && cl < llAtLast) ? 1 : 0;

  // BoS: detect transition 0→1 (compare bar last-1 state)
  const lhPrev = lastHighFF[last - 1];
  const llPrev = lastLowFF[last - 1];
  const close_above_prev = (lhPrev !== null && closes[last - 1] > lhPrev) ? 1 : 0;
  const close_below_prev = (llPrev !== null && closes[last - 1] < llPrev) ? 1 : 0;

  const break_of_structure_up   = (close_above === 1 && close_above_prev === 0) ? 1 : 0;
  const break_of_structure_down = (close_below === 1 && close_below_prev === 0) ? 1 : 0;

  const change_of_character_up   = (break_of_structure_up   === 1 && structure_trend === -1) ? 1 : 0;
  const change_of_character_down = (break_of_structure_down === 1 && structure_trend ===  1) ? 1 : 0;

  const wick_above = (lhAtLast !== null && highs[last] > lhAtLast) ? 1 : 0;
  const wick_below = (llAtLast !== null && lows[last]  < llAtLast) ? 1 : 0;

  // Sweeps: wick crosses level but close reverses
  const sweep_high = (lhAtLast !== null && highs[last] > lhAtLast && cl < lhAtLast) ? 1 : 0;
  const sweep_low  = (llAtLast !== null && lows[last]  < llAtLast && cl > llAtLast) ? 1 : 0;

  // ── Range context ─────────────────────────────────────────────────────────────
  const bothFractalLevels = lhAtLast !== null && llAtLast !== null;
  const fractalRange = bothFractalLevels ? lhAtLast! - llAtLast! : NaN;

  const fractal_range_pct =
    bothFractalLevels && cl > 0 && fractalRange > 0 ? (fractalRange / cl) * 100 : NaN;

  const range_position =
    bothFractalLevels && fractalRange > 0
      ? Math.min(1, Math.max(0, (cl - llAtLast!) / fractalRange))
      : NaN;

  const compression = !isNaN(fractal_range_pct) ? (fractal_range_pct < 0.5 ? 1 : 0) : 0;

  // ── Validate: reject if any feature is NaN / Infinity ────────────────────────
  const vec: number[] = [
    return_1, return_3, return_5, return_10, return_20,
    volatility_10, volatility_20,
    candle_body_pct, candle_range_pct, upper_wick_pct, lower_wick_pct,
    volume_change_5, volume_zscore_20,
    price_vs_sma_10, price_vs_sma_20, price_vs_sma_50,
    price_vs_ema_10, price_vs_ema_20, price_vs_ema_50,
    sma_20_slope, ema_20_slope,
    rsi_14,
    macd_line, macd_signal, macd_hist,
    atr_14,
    bollinger_position_20, bollinger_width_20,
    high_low_position_20, high_low_position_50,
    higher_high, lower_high, higher_low, lower_low,
    structure_trend,
    break_of_structure_up, break_of_structure_down,
    change_of_character_up, change_of_character_down,
    close_above, close_below,
    wick_above, wick_below,
    sweep_high, sweep_low,
    range_position, fractal_range_pct,
    compression,
    barsSinceHigh, barsSinceLow,
    dist_high, dist_low,
  ];

  if (vec.length !== PA_FEATURE_NAMES.length) {
    return {
      available: false,
      reason: `Experimental model unavailable: feature mismatch (${vec.length} vs ${PA_FEATURE_NAMES.length})`,
    };
  }

  for (let i = 0; i < vec.length; i++) {
    if (!Number.isFinite(vec[i])) {
      return {
        available: false,
        reason: `Experimental model unavailable: feature "${PA_FEATURE_NAMES[i]}" is not finite (insufficient history or no confirmed fractals)`,
      };
    }
  }

  return { available: true, features: vec };
}
