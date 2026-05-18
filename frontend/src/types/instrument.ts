export type Instrument = {
  id: number;
  ticker: string;
  name: string;
  market: string | null;
  board: string | null;
  currency: string | null;
  is_active: boolean;
};
