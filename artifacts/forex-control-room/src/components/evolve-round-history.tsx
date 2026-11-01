import { useEffect, useRef, useState } from 'react';
import { useGetEvolveHistory } from '@workspace/api-client-react';
import type { EvolveArchivedRound } from '@workspace/api-client-react';
import EvolveRoundDetails, { roundDecisionLabel } from './evolve-round-details';

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
  return <details
    className="evolve-history-row"
    data-testid={`evolve-history-row-${round.file}`}
    data-evolve-archive-file={round.file}
  >
    <summary data-testid={`evolve-history-toggle-${round.file}`}>
      <time><span className="evolve-mobile-label">DATE · UTC</span>{formatTimestamp(round.timestamp_utc)}</time>
      <strong className={round.eligible ? 'is-eligible' : 'is-ineligible'}>
        <span className="evolve-mobile-label">DECISION</span>
        {roundDecisionLabel(round.decision, round.eligible)}
      </strong>
      <span className="evolve-history-diff">
        <span className="evolve-mobile-label">PROPOSAL DIFF</span>{round.diff_summary}
      </span>
      <span><span className="evolve-mobile-label">EXPECTANCY</span>EXP {formatExpectancy(round.validation_expectancy)}</span>
      <span>
        <span className="evolve-mobile-label">PROVIDER / KEY</span>
        {round.provider ? `${round.provider} / KEY ${round.key_index ?? '—'}` : 'PROVIDER —'}
      </span>
      <span><span className="evolve-mobile-label">TOKEN USAGE</span>{formatTokens(round.token_usage)}</span>
    </summary>
    <div className="evolve-history-detail">
      <EvolveRoundDetails round={round}/>
    </div>
  </details>;
}

export default function EvolveRoundHistory({ refreshKey }: { refreshKey: string | null }) {
  const history = useGetEvolveHistory();
  const [archiveFile, setArchiveFile] = useState<string | null>(null);
  const pendingArchive = useRef<{ file: string; retries: number } | null>(null);

  useEffect(() => {
    if (refreshKey) void history.refetch();
  }, [refreshKey, history.refetch]);

  useEffect(() => {
    const openArchive = (event: Event) => {
      const file = (event as CustomEvent<{ file?: unknown }>).detail?.file;
      if (typeof file !== 'string') return;
      pendingArchive.current = { file, retries: 0 };
      setArchiveFile(file);
    };
    window.addEventListener('forex-open-evolve-archive', openArchive);
    return () => window.removeEventListener('forex-open-evolve-archive', openArchive);
  }, []);

  const rounds = history.data?.rounds ?? [];

  useEffect(() => {
    if (!archiveFile) return;
    const panel = document.querySelector<HTMLDetailsElement>('[data-testid="evolve-history-panel"]');
    if (panel) panel.open = true;
    const row = Array.from(
      document.querySelectorAll<HTMLElement>('[data-evolve-archive-file]'),
    ).find(item => item.dataset.evolveArchiveFile === archiveFile);
    if (row instanceof HTMLDetailsElement) {
      row.open = true;
      row.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
      pendingArchive.current = null;
      setArchiveFile(null);
      return;
    }
    if (pendingArchive.current?.file === archiveFile && pendingArchive.current.retries < 1) {
      pendingArchive.current.retries += 1;
      void history.refetch();
      return;
    }
    pendingArchive.current = null;
    setArchiveFile(null);
  }, [archiveFile, history.data, history.refetch]);

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
        {rounds.length > 0 && <div className="evolve-history-columns" aria-hidden="true">
          <span>DATE · UTC</span>
          <span>DECISION</span>
          <span>PROPOSAL DIFF</span>
          <span>EXPECTANCY</span>
          <span>PROVIDER / KEY</span>
          <span>TOKEN USAGE</span>
        </div>}
        {rounds.map(round => <ArchivedRoundRow key={round.file} round={round}/>)}
      </div>
    </details>
  </section>;
}
