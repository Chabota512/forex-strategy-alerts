import type { SimulatedTrade, SimulationSettings, SimulationSnapshot } from './types';
export const demoSettings: SimulationSettings = {
  enabled: true, startingBalance: 1000, riskPercent: 1, sizingMode: 'LOTS', lotSize: 0.01,
  rewardMultiple: 2, exitModel: 'candle_geometry', directionMode: 'normal', tradeDirection: 'both',
  swingVersion: 'v2', stopBodyWeight: 1, stopWickMultiplier: 2, targetRangeMultiplier: 3,
  geometryWeights: { bodyWeight: 1, wickMultiplier: 2, rangeMultiplier: 3 },
};
export const demoSnapshot: SimulationSnapshot = {
  enabled: true, balance: 1001.18, openTradeCount: 1, closedTradeCount: 3,
  equityCurve: [{equity:1000},{equity:1001.64},{equity:1001.12},{equity:1002.75},{equity:1001.18}],
  stats: { winRatePct: 66.7, wins: 2, losses: 1, avgR: 1.12, expectancyAfterCosts: 0.64,
    expectancyMoneyAfterCosts: 0.42, maxDrawdown: 0.52, maxDrawdownPct: 0.05, profitFactor: 2.08 },
};
export const demoTrades: SimulatedTrade[] = [
  { tradeId:'trade-1', entryTime:'2023-01-05T12:30:00Z', symbol:'XAUUSD', alertType:'SWING V2', direction:'BUY', lotSize:0.01, entryPrice:1865.20, stopPrice:1859.80, takeProfitPrice:1881.40, outcome:'WIN', exitReason:'TP', netPnl:1.62, pipPnl:162, priceMove:16.20, realizedR:3, cost:0.35 },
  { tradeId:'trade-2', entryTime:'2023-01-06T08:15:00Z', symbol:'XAUUSD', alertType:'SWING V2', direction:'SELL', lotSize:0.01, entryPrice:1868.40, stopPrice:1873.60, takeProfitPrice:1852.80, outcome:'LOSS', exitReason:'SL', netPnl:-0.52, pipPnl:-52, priceMove:-5.20, realizedR:-1, cost:0.35 },
  { tradeId:'trade-3', entryTime:'2023-01-06T14:45:00Z', symbol:'XAUUSD', alertType:'SWING V2', direction:'BUY', lotSize:0.01, entryPrice:1861.75, stopPrice:1857.10, takeProfitPrice:1875.70, outcome:'OPEN', exitReason:null, netPnl:0.08, pipPnl:8, priceMove:0.80, realizedR:0.17, cost:0.35 },
];
