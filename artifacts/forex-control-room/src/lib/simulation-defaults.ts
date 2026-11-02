import type { InstrumentSymbol } from '@workspace/api-client-react';

export const DEFAULT_LOT_SIZES: Record<InstrumentSymbol, number> = {
  XAUUSD: 0.01,
  GBPUSD: 0.1,
  USDJPY: 0.1,
  GBPJPY: 0.1,
  AUDJPY: 0.1,
};
