import { apiClient } from "./client";
import type { Instrument, InstrumentSearchResult } from "../types/instrument";

export function getInstruments(): Promise<Instrument[]> {
  return apiClient.get<Instrument[]>("/instruments");
}

export const fetchInstruments = getInstruments;

export function searchInstruments(
  query: string,
  limit: number = 20,
): Promise<InstrumentSearchResult[]> {
  const params = new URLSearchParams({
    query: query.trim(),
    limit: String(limit),
  });
  return apiClient.get<InstrumentSearchResult[]>(
    `/instruments/search?${params.toString()}`,
  );
}
