import type { MoexCandle } from '../api/moexDirect';
import type { AiSignalResult } from './types';
import { calculateFeatures } from './features';
import { runMockModel } from './mockModel';

const UNAVAILABLE: Omit<AiSignalResult, 'reason'> = {
  available:     false,
  direction:     'NO_TRADE',
  probabilities: { up: 0, down: 0, flat: 1 },
  confidence:    'low',
  horizonCandles: 3,
  modelVersion:  'mock_direction_v1',
};

// Synchronous — no network, no persistence.
// Pass merged candles (fullCandles + liveCandle if applicable) and current timeframe.
export function computeAiSignal(
  candles: MoexCandle[],
  _timeframe: string,
): AiSignalResult {
  if (!Array.isArray(candles) || candles.length === 0) {
    return { ...UNAVAILABLE, reason: 'No candle data available' };
  }

  try {
    const featureResult = calculateFeatures(candles);
    if (!featureResult.available) {
      return { ...UNAVAILABLE, reason: featureResult.reason };
    }
    return runMockModel(featureResult.features);
  } catch {
    return { ...UNAVAILABLE, reason: 'Feature calculation error' };
  }
}

// Merge liveCandle into fullCandles without mutating either array.
// Used before passing candles to computeAiSignal.
export function mergeWithLive(
  full: MoexCandle[],
  live: MoexCandle | null,
): MoexCandle[] {
  if (!live || full.length === 0) return full;
  const last = full[full.length - 1];
  if (live.begin > last.begin)    return [...full, live];
  if (live.begin === last.begin)  return [...full.slice(0, -1), live];
  return full;
}
