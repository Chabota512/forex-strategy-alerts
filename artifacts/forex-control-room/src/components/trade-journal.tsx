import { useMemo, useState } from 'react';
import { useGetTradeComment } from '@workspace/api-client-react';
import type { InstrumentSymbol, SimulatedTrade, TradeCommentResult } from '@workspace/api-client-react';

type OutcomeFilter = 'ALL' | 'WIN' | 'LOSS';
type SortKey = 'entryTime' | 'direction' | 'netPnl' | 'pipPnl' | 'netR';
const utc = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'UTC',
  }).format(date) + ' UTC';
};
const price = (value: number, symbol: InstrumentSymbol) => value.toLocaleString('en-US', {
  minimumFractionDigits: symbol === 'XAUUSD' ? 2 : symbol.endsWith('JPY') ? 3 : 5,
  maximumFractionDigits: symbol === 'XAUUSD' ? 2 : symbol.endsWith('JPY') ? 3 : 5,
});
const money = (value: number, currency: string) => `${value < 0 ? '−' : '+'}${currency} ${Math.abs(value).toFixed(2)}`;

export function TradeJournal({ trades, symbol, selectedTradeId, onSelect }: {
  trades: SimulatedTrade[];
  symbol: InstrumentSymbol;
  selectedTradeId: string | null;
  onSelect: (trade: SimulatedTrade) => void;
}) {
  const [filter, setFilter] = useState<OutcomeFilter>('ALL');
  const [sortKey, setSortKey] = useState<SortKey>('entryTime');
  const [descending, setDescending] = useState(true);
  const [notes, setNotes] = useState<Record<string, TradeCommentResult>>({});
  const [noteErrors, setNoteErrors] = useState<Record<string, boolean>>({});
  const hook = useGetTradeComment();
  const visible = useMemo(() => trades
    .filter(trade => filter === 'ALL' || trade.outcome === filter)
    .slice()
    .sort((a, b) => {
      const left = a[sortKey];
      const right = b[sortKey];
      const compared = typeof left === 'number' && typeof right === 'number'
        ? left - right : String(left).localeCompare(String(right));
      return descending ? -compared : compared;
    }), [trades, filter, sortKey, descending]);
  const closed = visible.filter(trade => trade.outcome !== 'OPEN');
  const wins = closed.filter(trade => trade.outcome === 'WIN').length;
  const net = closed.reduce((sum, trade) => sum + trade.netPnl, 0);
  const netR = closed.reduce((sum, trade) => sum + trade.netR, 0);
  const pnlCurrency = closed[0]?.pnlCurrency ?? (symbol.endsWith('JPY') ? 'JPY' : 'USD');
  const sort = (key: SortKey) => {
    if (sortKey === key) setDescending(value => !value);
    else { setSortKey(key); setDescending(key === 'entryTime'); }
  };
  const requestNote = (tradeId: string) => {
    if (notes[tradeId] || hook.isPending) return;
    setNoteErrors(current => ({ ...current, [tradeId]: false }));
    hook.mutate({ data: { tradeId } }, {
      onSuccess: result => setNotes(current => ({ ...current, [tradeId]: result })),
      onError: () => setNoteErrors(current => ({ ...current, [tradeId]: true })),
    });
  };
  return <aside className="trade-journal" aria-label="Trade journal" data-testid="trade-journal">
    <div className="alert-header journal-header">
      <div><strong>TRADE JOURNAL</strong><span>Simulated entries · UTC</span></div>
      <b>{visible.length.toString().padStart(2, '0')}</b>
    </div>
    <div className="journal-filters" role="group" aria-label="Filter journal outcomes">
      {(['ALL', 'WIN', 'LOSS'] as const).map(value => <button
        type="button" key={value} className={filter === value ? 'active' : ''}
        aria-pressed={filter === value} onClick={() => setFilter(value)}
        data-testid={`journal-filter-${value.toLowerCase()}`}
      >{value}</button>)}
    </div>
    <div className="journal-stats" data-testid="journal-filter-stats">
      <span>{closed.length} CLOSED</span><span>{wins} W / {closed.length - wins} L</span>
      <span>WR {closed.length ? `${(wins / closed.length * 100).toFixed(1)}%` : '—'}</span>
      <span>ΣR {netR >= 0 ? '+' : ''}{netR.toFixed(2)}</span>
      <b className={net >= 0 ? 'pnl-positive' : 'pnl-negative'}>{money(net, pnlCurrency)}</b>
    </div>
    <div className="journal-list">
      <div className="journal-sort">
        <button onClick={() => sort('entryTime')} data-testid="journal-sort-time">ENTRY UTC</button>
        <button onClick={() => sort('direction')} data-testid="journal-sort-direction">DIR</button>
        <button onClick={() => sort('netPnl')} data-testid="journal-sort-pnl">P&amp;L</button>
        <button onClick={() => sort('pipPnl')} data-testid="journal-sort-pips">PIPS</button>
        <button onClick={() => sort('netR')} data-testid="journal-sort-r">R</button>
      </div>
      {!visible.length ? <div className="journal-empty" data-testid="journal-empty">
        <strong>No {filter === 'ALL' ? '' : `${filter.toLowerCase()} `}trades yet</strong>
        <span>Entries appear here as the simulation reaches historical alerts.</span>
      </div> : visible.map(trade => <article
        key={trade.tradeId}
        className={`journal-trade${selectedTradeId === trade.tradeId ? ' selected' : ''}`}
        data-testid={`journal-trade-${trade.tradeId}`}
      >
        <button className="journal-trade-select" type="button" onClick={() => onSelect(trade)} aria-label={`Focus ${trade.direction} trade entered ${utc(trade.entryTime)}`} data-testid={`journal-select-${trade.tradeId}`}>
          <span className="journal-trade-top"><b>{trade.direction}</b><i className={`trade-outcome ${trade.outcome.toLowerCase()}`}>{trade.outcome}</i></span>
          <time>{utc(trade.entryTime)}</time>
          <span className="journal-prices">{price(trade.entryPrice, symbol)} <i>SL</i> {price(trade.stopPrice, symbol)} <i>TP</i> {price(trade.takeProfitPrice, symbol)}</span>
          <span className="journal-metrics">{trade.lotSize.toFixed(2)} lot · {money(trade.netPnl, trade.pnlCurrency)} · {trade.pipPnl >= 0 ? '+' : ''}{trade.pipPnl.toFixed(1)} pips</span>
          <span className="journal-metrics">Δ {trade.priceMove >= 0 ? '+' : ''}{price(trade.priceMove, symbol)} · {trade.realizedR >= 0 ? '+' : ''}{trade.realizedR.toFixed(2)}R</span>
          <span className="journal-metrics">{trade.pipSize} pip · {trade.contractSize.toLocaleString()} contract</span>
        </button>
        <button className="journal-ai" type="button" onClick={() => requestNote(trade.tradeId)} disabled={trade.outcome === 'OPEN' || Boolean(notes[trade.tradeId]) || hook.isPending} data-testid={`journal-ai-${trade.tradeId}`}>{hook.isPending && !notes[trade.tradeId] ? '…' : 'AI'}</button>
        {notes[trade.tradeId] && <div className={notes[trade.tradeId].available ? 'comment-result journal-note' : 'comment-error journal-note'} data-testid={`journal-note-${trade.tradeId}`}>
          {notes[trade.tradeId].available ? notes[trade.tradeId].comment : notes[trade.tradeId].message ?? 'Commentary unavailable.'}
          {notes[trade.tradeId].cached && <small> · CACHED</small>}
        </div>}
        {noteErrors[trade.tradeId] && !notes[trade.tradeId] && <div className="comment-error journal-note" role="alert" data-testid={`journal-note-error-${trade.tradeId}`}>Unavailable · click AI to retry.</div>}
      </article>)}
    </div>
  </aside>;
}
