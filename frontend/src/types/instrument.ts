export type Instrument = {
  id: number;
  ticker: string;
  name: string;
  engine: string | null;
  market: string | null;
  board: string | null;
  currency: string | null;
  is_active: boolean;
};

export type InstrumentSearchResult = {
  ticker: string;
  name: string;
  engine: string | null;
  market: string | null;
  board: string | null;
  currency: string | null;
  is_active: boolean;
  group: string | null;
};
