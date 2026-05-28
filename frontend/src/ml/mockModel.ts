import type { FeatureVector, AiSignalResult } from './types';

// ── Mock model constants ───────────────────────────────────────────────────
// Replace this section when integrating a real CatBoost model artifact.
// The real model should accept a numeric array in AI_FEATURE_NAMES order
// and return { up, down, flat } class probabilities.

const MOCK_MODEL_VERSION = "mock_direction_v1";
const HORIZON_CANDLES = 3;

// Deterministic heuristic: no randomness, same input → same output.
// Bullish/bearish score in [-1, 1] derived from features.
function heuristicScore(f: FeatureVector): number {
  let s = 0;

  // Short-term momentum
  s += f.return_1 > 0.005 ? 0.15 : f.return_1 < -0.005 ? -0.12 : 0;
  s += f.return_3 > 0.01  ? 0.15 : f.return_3 < -0.01  ? -0.12 : 0;
  s += f.return_5 > 0.015 ? 0.08 : f.return_5 < -0.015 ? -0.08 : 0;

  // Price vs trend lines
  s += f.price_vs_ema_20 > 0.015 ? 0.20 : f.price_vs_ema_20 < -0.015 ? -0.18 : 0;
  s += f.price_vs_sma_20 > 0.010 ? 0.10 : f.price_vs_sma_20 < -0.010 ? -0.10 : 0;

  // Slope direction
  s += f.ema_20_slope > 0 ? 0.10 : f.ema_20_slope < 0 ? -0.08 : 0;

  // Candle character
  s += f.lower_wick_pct > 0.35 ? 0.08 : 0;  // bullish hammer shadow
  s += f.upper_wick_pct > 0.35 ? -0.06 : 0; // bearish rejection wick

  // Range position
  s += f.high_low_position_20 > 0.75 ? 0.06 : f.high_low_position_20 < 0.25 ? -0.06 : 0;

  // Volume confirmation (weak signal only)
  s += f.volume_change_5 > 0.5 ? 0.04 : 0;

  return Math.max(-1, Math.min(1, s));
}

// Convert score to class probabilities.
function scoreToProbabilities(score: number): { up: number; down: number; flat: number } {
  let up: number;
  let down: number;
  let flat: number;

  if (score > 0.3) {
    up   = 0.38 + score * 0.14;
    down = 0.14;
    flat = 1 - up - down;
  } else if (score < -0.3) {
    down = 0.38 + Math.abs(score) * 0.14;
    up   = 0.14;
    flat = 1 - up - down;
  } else {
    // Near-zero score → flat dominates
    const absScore = Math.abs(score);
    flat = 0.52 + (0.3 - absScore) / 0.3 * 0.08;
    const rem = 1 - flat;
    up   = rem * (0.5 + score * 0.5);
    down = rem * (0.5 - score * 0.5);
  }

  // Clamp to valid range
  up   = Math.max(0.05, Math.min(0.82, up));
  down = Math.max(0.05, Math.min(0.82, down));
  flat = Math.max(0.05, Math.min(0.82, flat));

  // Normalize to sum = 1
  const total = up + down + flat;
  return {
    up:   Math.round((up   / total) * 1000) / 1000,
    down: Math.round((down / total) * 1000) / 1000,
    flat: Math.round((flat / total) * 1000) / 1000,
  };
}

// To replace: load CatBoost model artifact and call model.predict(featureArray) here.
export function runMockModel(features: FeatureVector): AiSignalResult {
  const score = heuristicScore(features);
  const probs = scoreToProbabilities(score);

  const maxProb = Math.max(probs.up, probs.down, probs.flat);

  // Conservative threshold: only signal directional trade if clear probability lead.
  const rawDirection = maxProb === probs.up ? 'LONG' : maxProb === probs.down ? 'SHORT' : 'NO_TRADE';
  const direction = maxProb >= 0.42 && rawDirection !== 'NO_TRADE' ? rawDirection : 'NO_TRADE';

  const confidence: AiSignalResult['confidence'] =
    maxProb >= 0.60 ? 'high' :
    maxProb >= 0.45 ? 'medium' : 'low';

  return {
    available:    true,
    direction,
    probabilities: probs,
    confidence,
    horizonCandles: HORIZON_CANDLES,
    modelVersion:   MOCK_MODEL_VERSION,
  };
}
