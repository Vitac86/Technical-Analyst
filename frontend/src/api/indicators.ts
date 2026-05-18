import { apiClient } from "./client";
import type { IndicatorValue } from "../types/indicator";

export async function getIndicatorValues(
  instrumentId: number,
  indicatorName: string,
  timeframe?: string,
): Promise<IndicatorValue[]> {
  const params = new URLSearchParams({
    instrument_id: String(instrumentId),
    indicator_name: indicatorName,
  });

  if (timeframe !== undefined) {
    params.set("timeframe", timeframe);
  }

  return apiClient.get<IndicatorValue[]>(`/indicators?${params.toString()}`);
}
