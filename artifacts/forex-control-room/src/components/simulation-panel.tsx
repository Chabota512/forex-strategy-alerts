import type { InstrumentSymbol, SimulatedTrade, SimulationSettings, SimulationSnapshot } from '@workspace/api-client-react';
import { useEffect, useId, useState } from 'react';
import { ChevronDown, ChevronUp, Maximize2 } from 'lucide-react';
import { DEFAULT_LOT_SIZES } from '@/lib/simulation-defaults';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import './equity-curve-dialog.css';

const money = (value?: number | null) => typeof value === 'number'
  ? `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  : '—';
const expectancyMoney = (value?: number | null) => typeof value === 'number'
  ? `${value < 0 ? '−' : '+'}$${Math.abs(value).toFixed(2)}`
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

function equityAxisMoney(value: number, tickStep: number): string {
  const fractionDigits = tickStep >= 10 ? 0 : tickStep >= 1 ? 1 : 2;
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

function EquityStrip({
  points,
  expanded = false,
}: {
  points: SimulationSnapshot['equityCurve'];
  expanded?: boolean;
}) {
  if (!points.length) return <div className="equity-empty">Equity points appear as the replay advances.</div>;
  const values = points.map(point => point.equity);
  const { min, max } = values.reduce(
    (bounds, value) => ({ min: Math.min(bounds.min, value), max: Math.max(bounds.max, value) }),
    { min: Number.POSITIVE_INFINITY, max: Number.NEGATIVE_INFINITY },
  );
  const spread = max - min;
  const padding = spread === 0 ? Math.max(Math.abs(max) * 0.01, 1) : spread * 0.08;
  const low = min - padding;
  const high = max + padding;
  const width = expanded ? 1000 : 100;
  const height = expanded ? 420 : 32;
  const plot = expanded
    ? { left: 88, right: 22, top: 26, bottom: 54 }
    : { left: 0, right: 0, top: 4, bottom: 4 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const polyline = values.map((value, index) => {
    const x = plot.left + (values.length === 1 ? plotWidth / 2 : index / (values.length - 1) * plotWidth);
    const y = plot.top + ((high - value) / (high - low)) * plotHeight;
    return `${x},${y}`;
  }).join(' ');
  const latest = values[values.length - 1];
  return <svg
    className={`equity-svg${expanded ? ' is-expanded' : ''}`}
    viewBox={`0 0 ${width} ${height}`}
    preserveAspectRatio="none"
    role="img"
    aria-label={`Simulated equity curve with ${points.length} points, starting at ${money(values[0])} and latest at ${money(latest)}`}
    data-testid={expanded ? 'chart-equity-curve-expanded' : 'chart-equity-curve-compact'}
  >
    {expanded && Array.from({ length: 5 }, (_, index) => {
      const value = high - ((high - low) * index) / 4;
      const y = plot.top + (plotHeight * index) / 4;
      return <g key={`equity-grid-${index}`}>
        <line className="equity-grid-line" x1={plot.left} x2={width - plot.right} y1={y} y2={y}/>
        <text className="equity-axis-label" x={plot.left - 10} y={y + 4} textAnchor="end">{equityAxisMoney(value, (high - low) / 4)}</text>
      </g>;
    })}
    {!expanded && <line x1="0" x2="100" y1="28" y2="28" className="equity-baseline"/>}
    {expanded && <>
      <text className="equity-axis-label" x={plot.left} y={height - 28}>START</text>
      <text className="equity-axis-label" x={width - plot.right} y={height - 28} textAnchor="end">LATEST</text>
      <text className="equity-axis-label" x={width / 2} y={height - 8} textAnchor="middle">REPLAY PROGRESS</text>
    </>}
    <polyline points={polyline} className="equity-line"/>
  </svg>;
}

export function SimulationPanel({
  symbol, simulationCost, settings, snapshot, trades, filterScopeLabel,
  onSettingsChange, sessionActive,
}: {
  symbol: InstrumentSymbol;
  simulationCost: number;
  settings: SimulationSettings;
  snapshot: SimulationSnapshot | null;
  trades: SimulatedTrade[];
  filterScopeLabel?: string;
  onSettingsChange: (settings: SimulationSettings) => void;
  sessionActive: boolean;
}) {
  const [draftSettings, setDraftSettings] = useState(settings);
  const [geometryMultiplierInputs, setGeometryMultiplierInputs] = useState({
    stopBodyWeight: String(settings.stopBodyWeight),
    stopWickMultiplier: String(settings.stopWickMultiplier),
    targetRangeMultiplier: String(settings.targetRangeMultiplier),
  });
  const [tradesExpanded, setTradesExpanded] = useState(true);
  const tradesTableId = useId();
  useEffect(() => {
    setDraftSettings(settings);
    setGeometryMultiplierInputs({
      stopBodyWeight: String(settings.stopBodyWeight),
      stopWickMultiplier: String(settings.stopWickMultiplier),
      targetRangeMultiplier: String(settings.targetRangeMultiplier),
    });
  }, [settings]);
  const stats = snapshot?.stats;
  const equityValues = snapshot?.equityCurve.map(point => point.equity) ?? [];
  const equityBounds = equityValues.reduce(
    (bounds, value) => ({ high: Math.max(bounds.high, value), low: Math.min(bounds.low, value) }),
    { high: Number.NEGATIVE_INFINITY, low: Number.POSITIVE_INFINITY },
  );
  const equityHigh = equityValues.length ? equityBounds.high : null;
  const equityLow = equityValues.length ? equityBounds.low : null;
  const startingEquity = equityValues[0] ?? null;
  const latestEquity = equityValues.at(-1) ?? null;
  const totalCost = trades.reduce((sum, trade) => sum + trade.cost, 0);
  const formattedCost = simulationCost.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  });
  const change = (patch: Partial<SimulationSettings>) => setDraftSettings(current => ({
    ...current,
    ...patch,
    geometryWeights: {
      bodyWeight: patch.stopBodyWeight ?? current.stopBodyWeight,
      wickMultiplier: patch.stopWickMultiplier ?? current.stopWickMultiplier,
      rangeMultiplier: patch.targetRangeMultiplier ?? current.targetRangeMultiplier,
    },
  }));
  const positiveMultiplier = (value: string, fallback: number) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(0.01, parsed) : fallback;
  };
  const geometryMultipliers = {
    stopBodyWeight: positiveMultiplier(geometryMultiplierInputs.stopBodyWeight, draftSettings.stopBodyWeight),
    stopWickMultiplier: positiveMultiplier(geometryMultiplierInputs.stopWickMultiplier, draftSettings.stopWickMultiplier),
    targetRangeMultiplier: positiveMultiplier(geometryMultiplierInputs.targetRangeMultiplier, draftSettings.targetRangeMultiplier),
  };
  const settingsChanged = draftSettings.enabled !== settings.enabled
    || draftSettings.startingBalance !== settings.startingBalance
    || draftSettings.riskPercent !== settings.riskPercent
    || draftSettings.sizingMode !== settings.sizingMode
    || draftSettings.lotSize !== settings.lotSize
    || draftSettings.rewardMultiple !== settings.rewardMultiple
    || draftSettings.exitModel !== settings.exitModel
    || draftSettings.directionMode !== settings.directionMode
    || geometryMultipliers.stopBodyWeight !== settings.stopBodyWeight
    || geometryMultipliers.stopWickMultiplier !== settings.stopWickMultiplier
    || geometryMultipliers.targetRangeMultiplier !== settings.targetRangeMultiplier;
  const metricItems = [
    { label: 'WIN RATE', value: stats ? `${stats.winRatePct.toFixed(1)}%` : '—', note: `${stats?.wins ?? 0} W / ${stats?.losses ?? 0} L` },
    { label: 'AVERAGE R', value: stats ? `${stats.avgR.toFixed(2)}R` : '—', note: 'closed trades' },
    { label: 'EXPECTANCY · NET', value: stats ? `${stats.expectancyAfterCosts.toFixed(2)}R · ${expectancyMoney(stats.expectancyMoneyAfterCosts)}` : '—', note: 'after costs per trade' },
    { label: 'MAX DRAWDOWN', value: stats ? money(stats.maxDrawdown) : '—', note: stats ? `${stats.maxDrawdownPct.toFixed(2)}%` : 'peak-to-trough' },
    { label: 'PROFIT FACTOR', value: stats?.profitFactor === null || stats?.profitFactor === undefined ? '—' : stats.profitFactor.toFixed(2), note: 'net wins / losses' },
  ];

  return <section className="simulation-panel" aria-label="Historical risk reward simulation">
    <div className="sim-head">
      <div>
        <div className="sim-eyebrow"><span className={`sim-indicator${snapshot?.enabled ? ' is-on' : ''}`}/>{snapshot?.enabled ? 'SIMULATION ACTIVE' : 'SIMULATION SURFACE'}</div>
        <h2>Risk / reward replay</h2>
        <p>Results are calculated from the historical candle sequence shown above.</p>
        <p className="sim-active-mode" data-testid="simulation-active-mode">
          DIRECTION · {settings.directionMode.toUpperCase()} · SWING {settings.swingVersion}
        </p>
        {filterScopeLabel && <>
          <p className="sim-filter-scope" data-testid="simulation-filter-scope" aria-live="polite">
            {filterScopeLabel}
          </p>
          <p className="sim-filter-scope-note">
            Changes apply to newly processed alerts; existing trades remain. Rewinding rebuilds with current filters.
          </p>
        </>}
      </div>
      <label className="sim-enable">
        <span>SIM ENABLED</span>
        <input type="checkbox" checked={draftSettings.enabled} onChange={event => change({ enabled: event.target.checked })} data-testid="toggle-simulation-enabled"/>
      </label>
    </div>
    <div className={`sim-settings${draftSettings.exitModel === 'candle_geometry' ? ' is-geometry' : ' is-swept'}`}>
      {symbol === 'XAUUSD' && <label><span>DIRECTION</span><select
        value={draftSettings.directionMode}
        onChange={event => change({ directionMode: event.target.value as SimulationSettings['directionMode'] })}
        data-testid="select-simulation-direction-mode"
      >
        <option value="normal">NORMAL</option>
        <option value="inverted">INVERTED</option>
        <option value="adaptive">ADAPTIVE</option>
      </select></label>}
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
                    value={geometryMultiplierInputs.stopBodyWeight}
                    onChange={event => setGeometryMultiplierInputs(current => ({ ...current, stopBodyWeight: event.target.value }))}
                    data-testid="input-simulation-stop-body-weight"
                  />
                  <span>+ sweep wick ×</span>
                  <input
                    type="number" min="0.01" step="any"
                    aria-label="Stop wick multiplier"
                    value={geometryMultiplierInputs.stopWickMultiplier}
                    onChange={event => setGeometryMultiplierInputs(current => ({ ...current, stopWickMultiplier: event.target.value }))}
                    data-testid="input-simulation-stop-wick-multiplier"
                  />
                </div>
                <div className="sim-formula-row">
                  <span>Target = candle range ×</span>
                  <input
                    type="number" min="0.01" step="any"
                    aria-label="Target range multiplier"
                    value={geometryMultiplierInputs.targetRangeMultiplier}
                    onChange={event => setGeometryMultiplierInputs(current => ({ ...current, targetRangeMultiplier: event.target.value }))}
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
        <button type="button" onClick={() => onSettingsChange({ ...draftSettings, ...geometryMultipliers })} disabled={!settingsChanged} data-testid="button-apply-simulation-settings">APPLY &amp; RESET REPLAY</button>
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
        {snapshot?.equityCurve.length
          ? <Dialog>
              <DialogTrigger asChild>
                <button
                  type="button"
                  className="equity-expand-trigger"
                  aria-label="Expand equity curve"
                  title="Click to expand equity curve"
                  data-testid="button-expand-equity-curve"
                >
                  <EquityStrip points={snapshot.equityCurve}/>
                  <span className="equity-expand-affordance" aria-hidden="true"><Maximize2 size={12}/></span>
                </button>
              </DialogTrigger>
              <DialogContent className="equity-dialog-content">
                <DialogHeader className="equity-dialog-header">
                  <DialogTitle className="equity-dialog-title">Equity curve</DialogTitle>
                  <DialogDescription className="equity-dialog-description">
                    Full replay history · {snapshot.equityCurve.length.toLocaleString()} points
                  </DialogDescription>
                </DialogHeader>
                <div className="equity-dialog-stats">
                  <div><span>STARTING EQUITY</span><strong>{money(startingEquity)}</strong></div>
                  <div><span>LATEST EQUITY</span><strong>{money(latestEquity)}</strong></div>
                  <div><span>HIGHEST</span><strong>{money(equityHigh)}</strong></div>
                  <div><span>LOWEST</span><strong>{money(equityLow)}</strong></div>
                </div>
                <EquityStrip points={snapshot.equityCurve} expanded/>
              </DialogContent>
            </Dialog>
          : snapshot
            ? <div className="equity-empty">Equity points appear as the replay advances.</div>
            : <div className="equity-empty">Curve is tied to replay progress.</div>}
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
    <h3 className="sim-trades-heading">
      <button
        className="sim-trades-heading-toggle"
        type="button"
        aria-label={tradesExpanded ? 'Collapse simulated trades table' : 'Expand simulated trades table'}
        aria-expanded={tradesExpanded}
        aria-controls={tradesTableId}
        onClick={() => setTradesExpanded(expanded => !expanded)}
      >
        <span className="sim-trades-heading-copy">
          <span className="sim-trades-heading-title">Simulated trades</span>
          <span className="sim-trades-heading-subtitle">Opened and resolved in candle replay order</span>
        </span>
        <span className="sim-trades-actions">
          <span className="sim-trade-count">{trades.length.toString().padStart(2, '0')} RECORDS</span>
          <span className="sim-trades-toggle">
            {tradesExpanded
              ? <ChevronUp size={12} aria-hidden="true"/>
              : <ChevronDown size={12} aria-hidden="true"/>}
            {tradesExpanded ? 'Hide table' : 'Show table'}
          </span>
        </span>
      </button>
    </h3>
    <div className="sim-table-wrap" id={tradesTableId} hidden={!tradesExpanded}>
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
