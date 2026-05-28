import { moexProvider } from './moexProvider';
import { bcsProvider } from './bcsProvider';
import type { MarketDataProvider, MarketDataProviderId } from './types';

const PROVIDERS: Record<MarketDataProviderId, MarketDataProvider> = {
  moex: moexProvider,
  bcs:  bcsProvider,
};

export function getProvider(id: MarketDataProviderId): MarketDataProvider {
  return PROVIDERS[id];
}
