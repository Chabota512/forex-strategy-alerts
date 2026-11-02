export type InstrumentSymbol = 'XAUUSD' | 'GBPUSD' | 'USDJPY' | 'GBPJPY' | 'AUDJPY';
export type SimulationSettings = {
  enabled: boolean; startingBalance: number; riskPercent: number; sizingMode: 'LOTS' | 'RISK_PERCENT';
  lotSize: number; rewardMultiple: number; exitModel: 'candle_geometry' | 'swept_level_atr';
  directionMode: 'normal' | 'inverted' | 'adaptive'; tradeDirection: 'both' | 'buy_only' | 'sell_only';
  swingVersion: string; stopBodyWeight: number; stopWickMultiplier: number; targetRangeMultiplier: number;
  geometryWeights: { bodyWeight: number; wickMultiplier: number; rangeMultiplier: number };
};
export type SimulationSnapshot = {
  enabled: boolean; balance: number; openTradeCount: number; closedTradeCount: number;
  equityCurve: Array<{ equity: number }>;
  stats: { winRatePct: number; wins: number; losses: number; avgR: number; expectancyAfterCosts: number;
    expectancyMoneyAfterCosts: number; maxDrawdown: number; maxDrawdownPct: number; profitFactor: number | null };
};
export type SimulatedTrade = {
  tradeId: string; entryTime: string; symbol: InstrumentSymbol; alertType: string; direction: 'BUY' | 'SELL';
  lotSize: number; entryPrice: number; stopPrice: number; takeProfitPrice: number;
  outcome: 'WIN' | 'LOSS' | 'OPEN'; exitReason: string | null; netPnl: number; pipPnl: number;
  priceMove: number; realizedR: number; cost: number;
};
export const DEFAULT_LOT_SIZES: Record<InstrumentSymbol, number> = {
  XAUUSD: 0.01, GBPUSD: 0.1, USDJPY: 0.1, GBPJPY: 0.1, AUDJPY: 0.1,
};
