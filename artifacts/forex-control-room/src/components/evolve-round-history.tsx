import { useEffect } from 'react';
import { useGetEvolveHistory } from '@workspace/api-client-react';
import type { EvolveArchivedRound } from '@workspace/api-client-react';

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'UTC',
  }).format(date) + ' UTC';
}

function formatExpectancy(value: number | null): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(4)} R`
    : '—';
}

function formatTokens(value: EvolveArchivedRound['token_usage']): string {
  if (!value) return 'TOKENS —';
  return `IN ${value.input ?? '—'} · OUT ${value.output ?? '—'} · TOTAL ${value.total ?? '—'}`;
}

function ArchivedRoundRow({ round }: { round: EvolveArchivedRound }) {
  return <details className="evolve-history-row" data-testid={`evolve-history-row-${round.file}`}>
    <summary data-testid={`evolve-history-toggle-${round.file}`}>
      <time>{formatTimestamp(round.timestamp_utc)}</time>
      <strong className={round.eligible ? 'is-eligible' : 'is-ineligible'}>
        {round.decision.toUpperCase()}
      </strong>
      <span className="evolve-history-diff">{round.diff_summary}</span>
      <span>EXP {formatExpectancy(round.validation_expectancy)}</span>
      <span>{round.provider ? `${round.provider} / KEY ${round.key_index ?? '—'}` : 'PROVIDER —'}</span>
      <span>{formatTokens(round.token_usage)}</span>
    </summary>
    <div className="evolve-history-detail">
      <div>ROUND {round.round} · {round.file}</div>
      <pre>{JSON.stringify(round.detail, null, 2)}</pre>
    </div>
  </details>;
}

export default function EvolveRoundHistory({ refreshKey }: { refreshKey: string | null }) {
  const history = useGetEvolveHistory();

  useEffect(() => {
    if (refreshKey) void history.refetch();
  }, [refreshKey, history.refetch]);

  const rounds = history.data?.rounds ?? [];

  return <section className="evolve-history" aria-label="Round history" data-testid="evolve-round-history">
    <details className="evolve-history-panel" data-testid="evolve-history-panel">
      <summary data-testid="evolve-history-toggle">
        ROUND HISTORY <span>{history.isLoading ? 'LOADING…' : `${rounds.length} ARCHIVED`}</span>
      </summary>
      <div className="evolve-history-content">
        {history.isError && <div className="evolve-history-message" role="alert">
          Could not load archived rounds.
          <button type="button" onClick={() => void history.refetch()}>RETRY</button>
        </div>}
        {history.isLoading && !history.data && <div className="evolve-history-message">Reading archived results…</div>}
        {!history.isError && history.data && rounds.length === 0
          && <div className="evolve-history-message">No archived evolution rounds.</div>}
        {rounds.map(round => <ArchivedRoundRow key={round.file} round={round}/>)}
      </div>
    </details>
  </section>;
}
