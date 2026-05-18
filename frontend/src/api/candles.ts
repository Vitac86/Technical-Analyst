import { apiClient } from "./client";
import type { Candle } from "../types/candle";

export function getCandles(
  instrumentId: number,
  timeframe: string,
): Promise<Candle[]> {
  const params = new URLSearchParams({
    instrument_id: String(instrumentId),
    timeframe,
  });

  return apiClient.get<Candle[]>(`/candles?${params.toString()}`);
}
