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

  const rows = await apiClient.get<IndicatorValue[]>(
    `/indicators?${params.toString()}`,
  );

  if (timeframe === undefined) {
    return rows;
  }

  return rows.filter((row) => row.timeframe === timeframe);
}
