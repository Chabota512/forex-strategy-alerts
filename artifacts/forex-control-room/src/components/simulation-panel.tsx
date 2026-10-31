import type { InstrumentSymbol, SimulatedTrade, SimulationSettings, SimulationSnapshot } from '@workspace/api-client-react';
import { useEffect, useState } from 'react';
import { DEFAULT_LOT_SIZES } from '@/lib/simulation-defaults';

const money = (value?: number | null) => typeof value === 'number'
  ? `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  : '—';
const price = (value?: number | null) => typeof value === 'number'
  ? value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 3 })
  : '—';
const time = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('en-GB', {
        day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
        hour12: false, timeZone: 'UTC',
      }).format(date);
};

function EquityStrip({ points }: { points: SimulationSnapshot['equityCurve'] }) {
  if (!points.length) return <div className="equity-empty">Equity points appear as the replay advances.</div>;
  const values = points.map(point => point.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const polyline = values.map((value, index) => {
    const x = values.length === 1 ? 0 : index / (values.length - 1) * 100;
    const y = 28 - ((value - min) / range) * 24;
    return `${x},${y}`;
  }).join(' ');
  return <svg className="equity-svg" viewBox="0 0 100 32" preserveAspectRatio="none" role="img" aria-label="Server-reported simulated equity curve">
    <line x1="0" x2="100" y1="28" y2="28" className="equity-baseline"/>
    <polyline points={polyline} className="equity-line"/>
  </svg>;
}

export function SimulationPanel({
  symbol, simulationCost, settings, snapshot, trades, onSettingsChange, sessionActive,
}: {
  symbol: InstrumentSymbol;
  simulationCost: number;
  settings: SimulationSettings;
  snapshot: SimulationSnapshot | null;
  trades: SimulatedTrade[];
  onSettingsChange: (settings: SimulationSettings) => void;
  sessionActive: boolean;
}) {
  const [draftSettings, setDraftSettings] = useState(settings);
  useEffect(() => setDraftSettings(settings), [settings]);
  const stats = snapshot?.stats;
  const totalCost = trades.reduce((sum, trade) => sum + trade.cost, 0);
  const formattedCost = simulationCost.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  });
  const change = (patch: Partial<SimulationSettings>) => setDraftSettings(current => ({ ...current, ...patch }));
  const settingsChanged = draftSettings.enabled !== settings.enabled
    || draftSettings.startingBalance !== settings.startingBalance
    || draftSettings.riskPercent !== settings.riskPercent
    || draftSettings.sizingMode !== settings.sizingMode
    || draftSettings.lotSize !== settings.lotSize
    || draftSettings.rewardMultiple !== settings.rewardMultiple
    || draftSettings.exitModel !== settings.exitModel
    || draftSettings.stopBodyWeight !== settings.stopBodyWeight
    || draftSettings.stopWickMultiplier !== settings.stopWickMultiplier
    || draftSettings.targetRangeMultiplier !== settings.targetRangeMultiplier;
  const positiveMultiplier = (value: string) => Math.max(0.01, Number(value) || 0);
  const metricItems = [
    { label: 'WIN RATE', value: stats ? `${stats.winRatePct.toFixed(1)}%` : '—', note: `${stats?.wins ?? 0} W / ${stats?.losses ?? 0} L` },
    { label: 'AVERAGE R', value: stats ? `${stats.avgR.toFixed(2)}R` : '—', note: 'closed trades' },
    { label: 'EXPECTANCY · NET', value: stats ? `${stats.expectancyAfterCosts.toFixed(2)}R` : '—', note: 'after costs per trade' },
    { label: 'MAX DRAWDOWN', value: stats ? money(stats.maxDrawdown) : '—', note: stats ? `${stats.maxDrawdownPct.toFixed(2)}%` : 'peak-to-trough' },
    { label: 'PROFIT FACTOR', value: stats?.profitFactor === null || stats?.profitFactor === undefined ? '—' : stats.profitFactor.toFixed(2), note: 'net wins / losses' },
  ];

  return <section className="simulation-panel" aria-label="Historical risk reward simulation">
    <div className="sim-head">
      <div>
        <div className="sim-eyebrow"><span className={`sim-indicator${snapshot?.enabled ? ' is-on' : ''}`}/>{snapshot?.enabled ? 'SIMULATION ACTIVE' : 'SIMULATION SURFACE'}</div>
        <h2>Risk / reward replay</h2>
        <p>Results are calculated from the historical candle sequence shown above.</p>
      </div>
      <label className="sim-enable">
        <span>SIM ENABLED</span>
        <input type="checkbox" checked={draftSettings.enabled} onChange={event => change({ enabled: event.target.checked })} data-testid="toggle-simulation-enabled"/>
      </label>
    </div>
    <div className={`sim-settings${draftSettings.exitModel === 'candle_geometry' ? ' is-geometry' : ' is-swept'}`}>
      <div className="sim-exit-settings">
        <label className="sim-exit-model-label">
          <span>EXIT MODEL</span>
          <select
            value={draftSettings.exitModel}
            onChange={event => change({ exitModel: event.target.value as SimulationSettings['exitModel'] })}
            data-testid="select-simulation-exit-model"
          >
            <option value="candle_geometry">candle_geometry · Sweep candle bracket</option>
            <option value="swept_level_atr">swept_level_atr</option>
          </select>
        </label>
        {draftSettings.exitModel === 'candle_geometry'
          ? <div className="sim-method-panel">
              <strong className="sim-method-title">Sweep candle bracket</strong>
              <div className="sim-geometry-equations">
                <div className="sim-formula-row">
                  <span>Stop = body ×</span>
                  <input
                    type="number" min="0.01" step="any"
                    aria-label="Stop body weight"
                    value={draftSettings.stopBodyWeight}
                    onChange={event => change({ stopBodyWeight: positiveMultiplier(event.target.value) })}
                    data-testid="input-simulation-stop-body-weight"
                  />
                  <span>+ sweep wick ×</span>
                  <input
                    type="number" min="0.01" step="any"
                    aria-label="Stop wick multiplier"
                    value={draftSettings.stopWickMultiplier}
                    onChange={event => change({ stopWickMultiplier: positiveMultiplier(event.target.value) })}
                    data-testid="input-simulation-stop-wick-multiplier"
                  />
                </div>
                <div className="sim-formula-row">
                  <span>Target = candle range ×</span>
                  <input
                    type="number" min="0.01" step="any"
                    aria-label="Target range multiplier"
                    value={draftSettings.targetRangeMultiplier}
                    onChange={event => change({ targetRangeMultiplier: positiveMultiplier(event.target.value) })}
                    data-testid="input-simulation-target-range-multiplier"
                  />
                </div>
              </div>
              <div className="sim-entry-fixed" data-testid="text-simulation-entry-fixed">
                Entry: next candle open <b>FIXED</b>
              </div>
            </div>
          : <p className="sim-swept-note">
              Stop distance is floored at ATR(14); reward multiple sets the target distance.
            </p>}
      </div>
      <label><span>STARTING BALANCE</span><div className="sim-input-wrap"><b>$</b><input type="number" min="1" step="100" value={draftSettings.startingBalance} onChange={event => change({ startingBalance: Math.max(1, Number(event.target.value)) })} data-testid="input-simulation-balance"/></div></label>
      {draftSettings.exitModel === 'swept_level_atr' && <label><span>REWARD MULTIPLE</span><div className="sim-input-wrap"><input type="number" min="0.01" max="100" step="0.1" value={draftSettings.rewardMultiple} onChange={event => change({ rewardMultiple: Math.max(0.01, Math.min(100, Number(event.target.value))) })} data-testid="input-simulation-reward"/><b>R</b></div></label>}
      <label><span>POSITION SIZING</span><select
        className="sim-sizing-select"
        value={draftSettings.sizingMode}
        onChange={event => change({ sizingMode: event.target.value as SimulationSettings['sizingMode'] })}
        data-testid="select-sizing-mode"
      ><option value="LOTS">LOTS</option><option value="RISK_PERCENT">RISK%</option></select></label>
      {draftSettings.sizingMode === 'LOTS'
        ? <label><span>LOT SIZE · {symbol}</span><div className="sim-input-wrap"><input type="number" min="0.01" step="0.01" value={draftSettings.lotSize || DEFAULT_LOT_SIZES[symbol]} onChange={event => change({ lotSize: Math.max(0.01, Number(event.target.value) || DEFAULT_LOT_SIZES[symbol]) })} data-testid="input-simulation-lot-size"/><b>LOT</b></div></label>
        : <label><span>RISK / TRADE</span><div className="sim-input-wrap"><input type="number" min="0.01" max="100" step="0.1" value={draftSettings.riskPercent} onChange={event => change({ riskPercent: Math.max(0.01, Math.min(100, Number(event.target.value))) })} data-testid="input-simulation-risk"/><b>%</b></div></label>}
      <div className="sim-settings-action">
        <button type="button" onClick={() => onSettingsChange(draftSettings)} disabled={!settingsChanged} data-testid="button-apply-simulation-settings">APPLY &amp; RESET REPLAY</button>
        <div className="sim-reset-note" role="status" data-testid="status-simulation-settings">
          {sessionActive ? 'Apply to end this replay and use the settings on the next replay.' : 'Settings take effect on the next replay.'}
        </div>
      </div>
    </div>
    <div className="sim-dashboard">
      <div className="sim-balance-block">
        <span className="sim-label">BALANCE</span>
        <strong data-testid="value-simulation-balance">{money(snapshot?.balance ?? (settings.enabled ? settings.startingBalance : null))}</strong>
        <small>{snapshot ? `${snapshot.openTradeCount} OPEN · ${snapshot.closedTradeCount} CLOSED` : 'Awaiting replay snapshot'}</small>
      </div>
      <div className="sim-equity-block">
        <div className="equity-caption"><span>EQUITY CURVE</span><span>{snapshot?.equityCurve.length ?? 0} points</span></div>
        {snapshot ? <EquityStrip points={snapshot.equityCurve}/> : <div className="equity-empty">Curve is tied to replay progress.</div>}
      </div>
      {metricItems.map(item => <div className="sim-metric" key={item.label}>
        <span>{item.label}</span><strong>{item.value}</strong><small>{item.note}</small>
      </div>)}
    </div>
    <div className="sim-cost-row">
      <div className="sim-cost-item">
        <span><b>PER-UNIT COST</b><small>{symbol}</small></span>
        <strong data-testid="value-simulation-unit-cost">{formattedCost}</strong>
      </div>
      <div className="sim-cost-item">
        <span><b>CUMULATIVE {symbol} COST</b><small>Listed trades</small></span>
        <strong data-testid="value-simulation-cost">{money(totalCost)}</strong>
      </div>
    </div>
    <div className="sim-trades-heading">
      <div><h3>Simulated trades</h3><span>Opened and resolved in candle replay order</span></div>
      <span className="sim-trade-count">{trades.length.toString().padStart(2, '0')} RECORDS</span>
    </div>
    <div className="sim-table-wrap">
      <table className="sim-trade-table">
        <thead><tr>
          <th>ENTRY TIME · UTC</th><th>DIR.</th><th>SIZE</th><th>ENTRY</th><th>STOP LOSS</th><th>TAKE PROFIT</th><th>OUTCOME</th><th>NET P&amp;L</th>
        </tr></thead>
        <tbody>
        {trades.length === 0
            ? <tr><td className="sim-empty-cell" colSpan={8}>{settings.enabled && sessionActive ? 'No simulated entries have been produced at this replay position.' : 'Start a replay to populate candle-by-candle simulated results.'}</td></tr>
            : [...trades].reverse().map(trade => <tr key={trade.tradeId} data-testid={`row-simulated-trade-${trade.tradeId}`}>
              <td><span>{time(trade.entryTime)}</span><small>{trade.symbol} · {trade.alertType}</small></td>
              <td><span className={`trade-direction ${trade.direction.toLowerCase()}`}>{trade.direction}</span></td>
              <td>{trade.lotSize.toLocaleString('en-US', { maximumFractionDigits: 4 })} lot</td>
              <td>{price(trade.entryPrice)}</td>
              <td className="trade-sl">{price(trade.stopPrice)}</td>
              <td className="trade-tp">{price(trade.takeProfitPrice)}</td>
              <td><span className={`trade-outcome ${trade.outcome.toLowerCase()}`}>{trade.outcome}{trade.exitReason ? ` · ${trade.exitReason}` : ''}</span></td>
              <td className={trade.netPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}>{money(trade.netPnl)}<small className="sim-accounting-cell">{trade.pipPnl >= 0 ? '+' : ''}{trade.pipPnl.toFixed(1)} pips · Δ {trade.priceMove.toFixed(symbol === 'XAUUSD' ? 2 : 5)} · {trade.realizedR.toFixed(2)}R</small></td>
            </tr>)}
        </tbody>
      </table>
    </div>
    <div className="sim-footer-note">Simulation only. No orders are placed. Historical outcomes do not establish future performance.</div>
  </section>;
}
