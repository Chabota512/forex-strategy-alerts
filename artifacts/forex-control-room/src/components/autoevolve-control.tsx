import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  getGetAutoevolveStatusQueryKey,
  getGetEvolvePolicyHistoryQueryKey,
  getGetEvolvePolicyQueryKey,
  useGetAutoevolveStatus,
  useGetEvolvePolicyHistory,
  useRestoreEvolvePolicy,
  useStartAutoevolve,
  useStopAutoevolve,
} from '@workspace/api-client-react';
import type { AutoevolveStatus, EvolvePolicyVersion } from '@workspace/api-client-react';
import './autoevolve-control.css';

const ALERT_STORAGE_KEY = 'forex-lab-autoevolve-dismissed-alerts';
type SwingVersion = 'V1' | 'V2';

function readDismissedAlerts(): string[] {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(ALERT_STORAGE_KEY) ?? '[]');
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
  } catch {
    return [];
  }
}

function displayTime(value: string | null | undefined): string {
  if (!value) return 'TIME —';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'UTC',
  }).format(date) + ' UTC';
}

function keyLabel(value: string | undefined): string {
  if (!value || value === 'unknown') return '—';
  return value.toUpperCase();
}

function policySettings(version: EvolvePolicyVersion): string {
  return JSON.stringify(version.settings);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export default function AutoevolveControl({
  datasetStatus,
}: {
  datasetStatus: 'checking' | 'approved' | 'unavailable';
}) {
  const queryClient = useQueryClient();
  const [swingVersion, setSwingVersion] = useState<SwingVersion>('V2');
  const [dismissedAlerts, setDismissedAlerts] = useState<string[]>(readDismissedAlerts);
  const [restoreVersion, setRestoreVersion] = useState<number | null>(null);
  const lastJobId = useRef<string | null>(null);

  const statusQuery = useGetAutoevolveStatus({
    query: {
      queryKey: getGetAutoevolveStatusQueryKey(),
      refetchInterval: 1200,
    },
  });
  const historyQuery = useGetEvolvePolicyHistory({
    query: { queryKey: getGetEvolvePolicyHistoryQueryKey() },
  });
  const startMutation = useStartAutoevolve();
  const stopMutation = useStopAutoevolve();
  const restoreMutation = useRestoreEvolvePolicy();
  const status = statusQuery.data;
  const versions = historyQuery.data?.versions ?? [];
  const persistentAlert = useMemo(
    () => status?.alerts.find(alert => alert.persistent) ?? null,
    [status?.alerts],
  );
  const popupAlert = useMemo(
    () => status?.alerts.find(alert => !dismissedAlerts.includes(alert.id)) ?? null,
    [dismissedAlerts, status?.alerts],
  );
  const currentVersion = versions.find(version => version.is_current)?.version;
  const isRunning = Boolean(status?.running);
  const startDisabled = datasetStatus !== 'approved'
    || isRunning || startMutation.isPending || statusQuery.isError;
  const showStart = !isRunning;

  useEffect(() => {
    const jobId = status?.active_job_id;
    if (!jobId || jobId === lastJobId.current) return;
    lastJobId.current = jobId;
    window.dispatchEvent(
      new CustomEvent('forex-autoevolve-job', { detail: { jobId } }),
    );
  }, [status?.active_job_id]);

  const refreshDaemon = () => {
    void queryClient.invalidateQueries({ queryKey: getGetAutoevolveStatusQueryKey() });
  };
  const start = () => {
    startMutation.mutate(
      { data: { swingVersion } },
      { onSuccess: refreshDaemon },
    );
  };
  const stop = () => {
    stopMutation.mutate({ data: {} }, { onSuccess: refreshDaemon });
  };
  const dismissPopup = () => {
    if (!popupAlert) return;
    const next = [...new Set([...dismissedAlerts, popupAlert.id])].slice(-100);
    setDismissedAlerts(next);
    try {
      window.localStorage.setItem(ALERT_STORAGE_KEY, JSON.stringify(next));
    } catch {
      // The alert remains visible for this session if storage is unavailable.
    }
  };
  const restore = () => {
    if (restoreVersion === null) return;
    restoreMutation.mutate(
      { data: { version: restoreVersion } },
      {
        onSuccess: () => {
          setRestoreVersion(null);
          void queryClient.invalidateQueries({ queryKey: getGetEvolvePolicyQueryKey() });
          void queryClient.invalidateQueries({ queryKey: getGetEvolvePolicyHistoryQueryKey() });
        },
      },
    );
  };
  const openArchive = () => {
    const file = status?.last_result_file;
    if (!file) return;
    window.dispatchEvent(
      new CustomEvent('forex-open-evolve-archive', {
        detail: { file: file.split(/[\\/]/).pop() ?? file },
      }),
    );
  };

  return <>
    <section className="autoevolve-strip" aria-label="AUTOEVOLVE daemon status" data-testid="autoevolve-status-strip">
      <div className="autoevolve-strip-heading">
        <i className={`autoevolve-indicator is-${status?.state ?? 'loading'}`} aria-hidden="true"/>
        <strong>AUTOEVOLVE ({(status?.state ?? 'loading').toUpperCase()})</strong>
      </div>
      <div className="autoevolve-strip-summary" role="status" data-testid="autoevolve-summary">
        <span>round</span>
        <button
          type="button"
          className="autoevolve-round-link"
          onClick={openArchive}
          disabled={!status?.last_result_file}
          aria-label={`Open archived round ${status?.round ?? 0}`}
          data-testid="button-open-autoevolve-archive"
        >
          {status?.round ?? 0}
        </button>
        <span>· {status?.phase ?? 'loading'} · {status?.last_verdict ?? 'no rounds'} · keys: g1 {keyLabel(status?.keys.key1)} g2 {keyLabel(status?.keys.key2)}</span>
      </div>
      {persistentAlert && <span className="autoevolve-persistent-alert" role="alert" data-testid="autoevolve-persistent-alert">
        {persistentAlert.message}
      </span>}
      <details className="autoevolve-config">
        <summary data-testid="autoevolve-config-toggle">CONFIG</summary>
        <div className="autoevolve-config-panel">
          <label>
            SWING VERSION
            <select
              value={swingVersion}
              onChange={event => setSwingVersion(event.target.value as SwingVersion)}
              disabled={isRunning}
              data-testid="autoevolve-swing-version"
            >
              <option value="V2">V2 · BREAK-BASED</option>
              <option value="V1">V1 · AMPLITUDE-CST</option>
            </select>
          </label>
          <p>MISSION XAUUSD · PROVIDER GROQ ONLY · COOLDOWN {status?.cooldown_seconds ?? 60}s</p>
          <p>Research only. AUTOEVOLVE never promotes without owner approval or places orders.</p>
        </div>
      </details>
      {showStart
        ? <button type="button" className="autoevolve-action is-start" onClick={start} disabled={startDisabled} data-testid="button-start-autoevolve">
            {status?.state === 'paused-keys-depleted' ? 'RESUME' : 'START'}
          </button>
        : <button type="button" className="autoevolve-action is-stop" onClick={stop} disabled={stopMutation.isPending || status?.stop_requested} data-testid="button-stop-autoevolve">
            {status?.stop_requested ? 'STOPPING…' : 'STOP'}
          </button>}
      {showStart && datasetStatus === 'checking' && <span className="autoevolve-dataset-note" role="status" data-testid="autoevolve-dataset-checking">
        CHECKING XAUUSD DATASET…
      </span>}
      {showStart && datasetStatus === 'unavailable' && <span className="autoevolve-dataset-note" role="status" data-testid="autoevolve-dataset-unavailable">
        AUTOEVOLVE needs an owner-approved XAUUSD dataset before it can start.
      </span>}
      {(statusQuery.isError || startMutation.isError || stopMutation.isError) && <span className="autoevolve-inline-error" role="alert">
        {startMutation.isError
          ? errorMessage(startMutation.error, 'AUTOEVOLVE could not start.')
          : stopMutation.isError
            ? errorMessage(stopMutation.error, 'AUTOEVOLVE could not stop.')
            : errorMessage(statusQuery.error, 'AUTOEVOLVE status could not be loaded.')}
      </span>}
    </section>

    <section className="policy-history" aria-label="SYSTEM INTELLIGENCE policy history" data-testid="policy-history-panel">
      <details>
        <summary>
          POLICY HISTORY
          <span>{historyQuery.isLoading ? 'LOADING…' : `${versions.length} VERSIONS · CURRENT V${currentVersion ?? '—'}`}</span>
        </summary>
        <div className="policy-history-content">
          {historyQuery.isError && <div role="alert">Could not load policy history.</div>}
          {!historyQuery.isError && !historyQuery.isLoading && versions.length === 0
            && <p>No policy versions are available.</p>}
          {versions.map(version => <article className="policy-version" key={version.version} data-testid={`policy-version-${version.version}`}>
            <header>
              <strong>POLICY V{version.version}{version.is_current ? ' · CURRENT' : ''}</strong>
              <time>{displayTime(version.created_at_utc)}</time>
            </header>
            <p>MODE {version.mode.toUpperCase()} · SOURCE CHAMPION {version.source_champion_version ?? '—'}
              {version.restored_from_version !== null && ` · RESTORED FROM V${version.restored_from_version}`}
            </p>
            <pre>{policySettings(version)}</pre>
            {!version.is_current && <button
              type="button"
              className="autoevolve-action is-restore"
              onClick={() => setRestoreVersion(version.version)}
              disabled={restoreMutation.isPending}
              data-testid={`button-restore-policy-${version.version}`}
            >
              RESTORE V{version.version}
            </button>}
          </article>)}
          {restoreMutation.isError && <div role="alert">Policy restore failed. The current policy was not changed.</div>}
        </div>
      </details>
    </section>

    {popupAlert && <div className="autoevolve-dialog-backdrop" role="presentation">
      <section
        className="autoevolve-alert-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="autoevolve-alert-title"
        data-testid="autoevolve-alert-dialog"
      >
        <span className="autoevolve-kicker">OWNER ALERT</span>
        <h2 id="autoevolve-alert-title">{popupAlert.kind.replace(/-/g, ' ').toUpperCase()}</h2>
        <p>{popupAlert.message}</p>
        <button type="button" className="autoevolve-action is-start" onClick={dismissPopup} data-testid="button-dismiss-autoevolve-alert">
          ACKNOWLEDGE
        </button>
      </section>
    </div>}

    {restoreVersion !== null && <div className="autoevolve-dialog-backdrop" role="presentation">
      <section
        className="autoevolve-alert-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="policy-restore-title"
        aria-describedby="policy-restore-copy"
        data-testid="policy-restore-confirmation"
      >
        <span className="autoevolve-kicker">OWNER CONFIRMATION</span>
        <h2 id="policy-restore-title">Restore policy V{restoreVersion}?</h2>
        <p id="policy-restore-copy">This creates a new immutable policy version using V{restoreVersion} settings. The champion strategy is unchanged.</p>
        <div className="autoevolve-dialog-actions">
          <button type="button" className="autoevolve-action is-stop" onClick={() => setRestoreVersion(null)} disabled={restoreMutation.isPending}>
            CANCEL
          </button>
          <button type="button" className="autoevolve-action is-start" onClick={restore} disabled={restoreMutation.isPending} data-testid="button-confirm-policy-restore">
            {restoreMutation.isPending ? 'RESTORING…' : 'CONFIRM RESTORE'}
          </button>
        </div>
      </section>
    </div>}
  </>;
}
