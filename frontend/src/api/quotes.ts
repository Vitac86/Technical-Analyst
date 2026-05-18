import { apiClient } from "./client";
import type { QuoteSnapshot } from "../types/quote";

export function getMoexQuote(params: {
  ticker: string;
  engine: string;
  market: string;
  board: string;
}): Promise<QuoteSnapshot> {
  const qs = new URLSearchParams(params).toString();
  return apiClient.get<QuoteSnapshot>(`/quotes/moex?${qs}`);
}
