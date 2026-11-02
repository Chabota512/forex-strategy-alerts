import type {
  Alert,
  InstrumentSymbol,
  SimulatedTrade,
} from '@workspace/api-client-react';
import { ManualDecisionLog, type ManualDecisionRecord } from '@/components/manual-decision-log';

function formatPrice(value: number, symbol: InstrumentSymbol) {
  const digits = symbol === 'XAUUSD' ? 2 : symbol.endsWith('JPY') ? 3 : 5;
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : `${new Intl.DateTimeFormat('en-GB', {
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
        timeZone: 'UTC',
      }).format(date)} UTC`;
}

type ManualTradesPanelProps = {
  symbol: InstrumentSymbol;
  manualMode: boolean;
  onManualModeChange: (enabled: boolean) => void;
  manualComment: string;
  onManualCommentChange: (comment: string) => void;
  manualSessionReady: boolean;
  activeAlert: Alert | null;
  undecidedCount: number;
  simulationEnabled: boolean;
  actionError: string;
  onTakeBuy: () => void;
  onTakeSell: () => void;
  onSkip: () => void;
  decisions: ManualDecisionRecord[];
  activeTrades: SimulatedTrade[];
};

export function ManualTradesPanel({
  symbol,
  manualMode,
  onManualModeChange,
  manualComment,
  onManualCommentChange,
  manualSessionReady,
  activeAlert,
  undecidedCount,
  simulationEnabled,
  actionError,
  onTakeBuy,
  onTakeSell,
  onSkip,
  decisions,
  activeTrades,
}: ManualTradesPanelProps) {
  const supported = symbol === 'XAUUSD';
  const recentTrades = [...activeTrades].sort((left, right) => right.entryIndex - left.entryIndex);
  const status = !supported
    ? 'MANUAL TRADING IS AVAILABLE FOR XAUUSD ONLY.'
    : !manualMode
      ? 'ENABLE MANUAL MODE, THEN RUN THE ENGINE TO REVIEW ALERTS.'
      : !manualSessionReady
        ? 'RUN ENGINE TO START A MANUAL REPLAY.'
        : !simulationEnabled
          ? 'ENABLE SIMULATION TO TAKE A TRADE. SKIPPING ALERTS IS STILL AVAILABLE.'
          : activeAlert
            ? `CURRENT ${activeAlert.direction} ALERT · ${formatTime(activeAlert.time)} · ${undecidedCount} UNDECIDED`
            : 'NO UNDECIDED ALERT AT THIS CANDLE · ADVANCE TO THE NEXT ALERT.';

  const takeDisabled = (side: 'BUY' | 'SELL') =>
    !supported
    || !manualMode
    || !manualSessionReady
    || !simulationEnabled
    || !activeAlert
    || activeAlert.direction !== side;

  return <aside
    id="panel-manual-trades"
    role="tabpanel"
    aria-labelledby="tab-manual-trades"
    className="manual-trades-pane"
    data-testid="manual-trades-panel"
  >
    <div className="alert-header manual-trades-header">
      <div><strong>MANUAL TRADES</strong><span>Owner-selected entries · XAUUSD</span></div>
      <b data-testid="manual-active-trade-count">{recentTrades.length.toString().padStart(2, '0')}</b>
    </div>

    <div className="manual-trades-content">
      <section
        className={`manual-replay-controls${manualMode ? ' is-active' : ''}`}
        aria-label="Manual replay controls"
        data-testid="manual-replay-controls"
      >
        <label className="manual-mode-toggle">
          <input
            type="checkbox"
            checked={manualMode}
            disabled={!supported}
            onChange={event => onManualModeChange(event.target.checked)}
            data-testid="toggle-manual-replay"
          />
          <span>MANUAL MODE</span>
        </label>
        {manualMode && supported
          ? <>
              <p>Paused replay · N next · P previous · B/S take the matching alert side · K skip. Moving past a candle records undecided alerts there as skipped.</p>
              <div className="manual-decision-actions">
                <textarea
                  value={manualComment}
                  onChange={event => onManualCommentChange(event.target.value.slice(0, 500))}
                  maxLength={500}
                  placeholder="Optional decision comment"
                  aria-label="Optional manual decision comment"
                  data-testid="input-manual-decision-comment"
                />
                <button
                  type="button"
                  disabled={takeDisabled('BUY')}
                  title={!manualSessionReady ? 'Start a manual replay to enable decisions.' : !simulationEnabled ? 'Enable simulation to take a manual trade.' : activeAlert?.direction !== 'BUY' ? 'TAKE BUY is available for a current BUY alert.' : 'Take the current BUY alert.'}
                  onClick={onTakeBuy}
                  data-testid="button-manual-take-buy"
                >TAKE BUY · B</button>
                <button
                  type="button"
                  disabled={takeDisabled('SELL')}
                  title={!manualSessionReady ? 'Start a manual replay to enable decisions.' : !simulationEnabled ? 'Enable simulation to take a manual trade.' : activeAlert?.direction !== 'SELL' ? 'TAKE SELL is available for a current SELL alert.' : 'Take the current SELL alert.'}
                  onClick={onTakeSell}
                  data-testid="button-manual-take-sell"
                >TAKE SELL · S</button>
                <button
                  type="button"
                  disabled={!manualSessionReady || !activeAlert}
                  title={!manualSessionReady ? 'Start a manual replay to enable decisions.' : !activeAlert ? 'No undecided alert at the current candle.' : 'Skip the current alert.'}
                  onClick={onSkip}
                  data-testid="button-manual-skip"
                >SKIP ALERT · K</button>
              </div>
              <div className="manual-decision-status" role="status" data-testid="manual-decision-status">
                {status}
              </div>
              {actionError && <div role="alert" className="inline-error" data-testid="error-manual-decision">{actionError}</div>}
            </>
          : <p>{supported
              ? 'Manual replay is paused by default when enabled. Automated playback remains unchanged otherwise.'
              : 'Switch to XAUUSD to use manual replay and take trades.'}</p>}
      </section>

      <section className="manual-active-positions" aria-label="Active manual positions">
        <div className="manual-section-heading">
          <span>OPEN MANUAL POSITIONS</span>
          <b>{recentTrades.length.toString().padStart(2, '0')}</b>
        </div>
        {!recentTrades.length
          ? <p className="manual-trades-empty">Manual trades appear here after you take an alert and advance to its next candle.</p>
          : recentTrades.map(trade => <article
              key={trade.tradeId}
              className="manual-position"
              data-testid={`manual-active-trade-${trade.tradeId}`}
            >
              <div className="manual-position-heading">
                <strong className={`manual-position-direction is-${trade.direction.toLowerCase()}`}>{trade.direction}</strong>
                <span>OPEN · {formatTime(trade.entryTime)}</span>
              </div>
              <dl className="manual-position-levels">
                <div><dt>ENTRY</dt><dd>{formatPrice(trade.entryPrice, symbol)}</dd></div>
                <div><dt>STOP</dt><dd>{formatPrice(trade.stopPrice, symbol)}</dd></div>
                <div><dt>TARGET</dt><dd>{formatPrice(trade.takeProfitPrice, symbol)}</dd></div>
              </dl>
              <div className="manual-position-size">{trade.lotSize.toFixed(2)} LOT · {trade.alertType}</div>
            </article>)}
        <p className="manual-chart-hint">ENTRY, STOP, AND TARGET LINES ARE SHOWN ON THE CHART WHILE THE POSITION IS OPEN.</p>
      </section>

      <ManualDecisionLog records={decisions}/>
    </div>
  </aside>;
}
