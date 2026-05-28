export type AiSignalDirection = "LONG" | "SHORT" | "NO_TRADE";

export interface AiSignalResult {
  available: boolean;
  direction: AiSignalDirection;
  probabilities: {
    up: number;
    down: number;
    flat: number;
  };
  confidence: "low" | "medium" | "high";
  horizonCandles: number;
  modelVersion: string;
  reason?: string;
}

// Ordered feature names — keep this order stable across mock and future CatBoost model.
// When training the real model on PC, produce features in exactly this order.
export const AI_FEATURE_NAMES = [
  "return_1",
  "return_3",
  "return_5",
  "return_10",
  "volatility_10",
  "volatility_20",
  "candle_body_pct",
  "candle_range_pct",
  "upper_wick_pct",
  "lower_wick_pct",
  "volume_change_5",
  "volume_zscore_20",
  "price_vs_sma_20",
  "price_vs_ema_20",
  "sma_20_slope",
  "ema_20_slope",
  "high_low_position_20",
] as const;

export type FeatureName = (typeof AI_FEATURE_NAMES)[number];

export type FeatureVector = Record<FeatureName, number>;

export type FeatureResult =
  | { available: true; features: FeatureVector }
  | { available: false; reason: string };
