// Watchlist metadata only — never stores candles.

export type WatchlistAsset = {
  id: string; // "${engine}:${market}:${board}:${ticker}"
  ticker: string;
  name?: string;
  engine: string;
  market: string;
  board: string;
};

const LS_KEY = 'technicalAnalyst.mobile.watchlist';

const DEFAULT_WATCHLIST: WatchlistAsset[] = [
  { id: 'stock:shares:TQBR:SBER',          ticker: 'SBER',         name: 'Сбербанк',    engine: 'stock',    market: 'shares', board: 'TQBR' },
  { id: 'stock:shares:TQBR:GAZP',          ticker: 'GAZP',         name: 'Газпром',     engine: 'stock',    market: 'shares', board: 'TQBR' },
  { id: 'stock:shares:TQBR:LKOH',          ticker: 'LKOH',         name: 'ЛУКОЙЛ',      engine: 'stock',    market: 'shares', board: 'TQBR' },
  { id: 'stock:shares:TQBR:YNDX',          ticker: 'YNDX',         name: 'Яндекс',      engine: 'stock',    market: 'shares', board: 'TQBR' },
  { id: 'currency:selt:CETS:USD000UTSTOM', ticker: 'USD000UTSTOM', name: 'USD/RUB TOM', engine: 'currency', market: 'selt',   board: 'CETS' },
];

export function makeAssetId(engine: string, market: string, board: string, ticker: string): string {
  return `${engine}:${market}:${board}:${ticker}`;
}

export function loadWatchlist(): WatchlistAsset[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return DEFAULT_WATCHLIST;
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed) && parsed.length > 0) {
      // Strip any legacy `alias` field that older builds may have written.
      return (parsed as Array<Record<string, unknown>>).map(a => ({
        id:     String(a.id ?? ''),
        ticker: String(a.ticker ?? ''),
        name:   typeof a.name === 'string' ? a.name : undefined,
        engine: String(a.engine ?? ''),
        market: String(a.market ?? ''),
        board:  String(a.board ?? ''),
      })) as WatchlistAsset[];
    }
  } catch { /* unavailable */ }
  return DEFAULT_WATCHLIST;
}

export function saveWatchlist(list: WatchlistAsset[]): void {
  try { localStorage.setItem(LS_KEY, JSON.stringify(list)); } catch { /* unavailable */ }
}
