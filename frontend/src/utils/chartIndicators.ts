export type IndicatorCandle = {
  begin: string;
  close: number;
};

export type IndicatorPoint = {
  time: string | number;
  value: number;
};

export function calculateSma(candles: IndicatorCandle[], period: number): IndicatorPoint[] {
  if (!Number.isInteger(period) || period <= 0 || candles.length < period) return [];

  const points: IndicatorPoint[] = [];
  let rollingSum = 0;

  for (let i = 0; i < candles.length; i += 1) {
    const close = candles[i].close;
    if (!Number.isFinite(close)) return [];

    rollingSum += close;
    if (i >= period) rollingSum -= candles[i - period].close;

    if (i >= period - 1) {
      points.push({
        time: candles[i].begin,
        value: rollingSum / period,
      });
    }
  }

  return points;
}

export function calculateEma(candles: IndicatorCandle[], period: number): IndicatorPoint[] {
  if (!Number.isInteger(period) || period <= 0 || candles.length < period) return [];

  const seed = candles.slice(0, period);
  if (seed.some(c => !Number.isFinite(c.close))) return [];

  const points: IndicatorPoint[] = [];
  const multiplier = 2 / (period + 1);
  let ema = seed.reduce((sum, candle) => sum + candle.close, 0) / period;

  points.push({
    time: candles[period - 1].begin,
    value: ema,
  });

  for (let i = period; i < candles.length; i += 1) {
    const close = candles[i].close;
    if (!Number.isFinite(close)) return [];

    ema = (close - ema) * multiplier + ema;
    points.push({
      time: candles[i].begin,
      value: ema,
    });
  }

  return points;
}
