import type { MoexCandle } from '../api/moexDirect';

/**
 * Merges incoming (recent) candles into the existing candle array.
 * Deduplicates by timestamp, replaces existing candle with same timestamp,
 * appends newer candles. Returns sorted ascending by begin.
 * Does not mutate input arrays.
 */
export function mergeLiveCandles(
  existing: MoexCandle[],
  incoming: MoexCandle[],
): MoexCandle[] {
  if (incoming.length === 0) return existing;

  const map = new Map<string, MoexCandle>();
  for (const c of existing) map.set(c.begin, c);
  for (const c of incoming) map.set(c.begin, c);

  return [...map.values()].sort((a, b) =>
    a.begin < b.begin ? -1 : a.begin > b.begin ? 1 : 0,
  );
}
