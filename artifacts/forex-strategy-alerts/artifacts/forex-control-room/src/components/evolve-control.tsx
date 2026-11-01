import { useEffect, useMemo, useState } from 'react';
import {
  getGetEvolveStatusQueryKey,
  useDecideEvolveJob,
  useGetEvolveStatus,
  useRunEvolveRound,
} from '@workspace/api-client-react';
import type {
  EvolveJobStatus,
  EvolveMissionConfig,
  InstrumentSymbol,
} from '@workspace/api-client-react';
import EvolveRoundHistory from './evolve-round-history';
import './evolve-control.css';

const STORAGE_KEY = 'forex-lab-evolve-round';
const CONFIG_STORAGE_KEY = 'forex-lab-evolve-mission-config';
type PersistedState = { jobId: string | null };
const DEFAULT_MISSION_CONFIG: EvolveMissionConfig = {
  symbol: 'XAUUSD',
  trainingYears: [2023, 2024],
  validationYears: [2025],
  exitModel: 'swept_level_atr',
  directionMode: 'normal',
  swingVersion: 'V2',
  providers: ['GEMINI', 'GROQ'],
  objective: 'maximize_profit',
};
const MISSION_YEARS = [2023, 2024, 2025] as const;
const MISSION_SYMBOLS: InstrumentSymbol[] = ['XAUUSD', 'GBPUSD', 'USDJPY', 'GBPJPY', 'AUDJPY'];

function readPersistedState(): PersistedState {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (!stored) return { jobId: null };
    const parsed = JSON.parse(stored) as Partial<PersistedState>;
    return {
      jobId: typeof parsed.jobId === 'string' ? parsed.jobId : null,
    };
  } catch {
    return { jobId: null };
  }
}

function readPersistedMissionConfig(): EvolveMissionConfig {
  try {
    const stored = window.localStorage.getItem(CONFIG_STORAGE_KEY);
    if (!stored) return DEFAULT_MISSION_CONFIG;
    const parsed = JSON.parse(stored) as Partial<EvolveMissionConfig>;
    const symbol = MISSION_SYMBOLS.includes(parsed.symbol as InstrumentSymbol)
      ? parsed.symbol as EvolveMissionConfig['symbol']
      : DEFAULT_MISSION_CONFIG.symbol;
    const validYears = (years: unknown) => Array.isArray(years)
      ? years.filter((year): year is 2023 | 2024 | 2025 =>
        MISSION_YEARS.includes(year as 2023 | 2024 | 2025))
      : [];
    const providers = Array.isArray(parsed.providers)
      ? parsed.providers.filter((provider): provider is 'GEMINI' | 'GROQ' =>
        provider === 'GEMINI' || provider === 'GROQ')
      : DEFAULT_MISSION_CONFIG.providers;
    return {
      ...DEFAULT_MISSION_CONFIG,
      symbol,
      trainingYears: validYears(parsed.trainingYears),
      validationYears: validYears(parsed.validationYears),
      exitModel: parsed.exitModel === 'candle_geometry' ? 'candle_geometry' : 'swept_level_atr',
      directionMode: symbol === 'XAUUSD'
        ? parsed.directionMode === 'inverted' || parsed.directionMode === 'adaptive'
          ? parsed.directionMode
          : 'normal'
        : 'normal',
      swingVersion: symbol === 'XAUUSD'
        ? parsed.swingVersion === 'V1' ? 'V1' : 'V2'
        : 'V1',
      providers,
      objective: parsed.objective === 'develop_better_filter'
        ? 'develop_better_filter'
        : 'maximize_profit',
    };
  } catch {
    return DEFAULT_MISSION_CONFIG;
  }
}

function missionConfigError(config: EvolveMissionConfig): string | null {
  if (!config.trainingYears.length) return 'Select at least one training year.';
  if (!config.validationYears.length) return 'Select at least one validation year.';
  if (Math.min(...config.validationYears) <= Math.max(...config.trainingYears)) {
    return 'Validation years must be later than every training year.';
  }
  if (!config.providers.length) return 'Select at least one model provider.';
  return null;
}

function toggleYear(years: number[], year: number): number[] {
  return years.includes(year)
    ? years.filter(value => value !== year)
    : [...years, year].sort((a, b) => a - b);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function errorStatus(error: unknown): number | undefined {
  return isRecord(error) && typeof error.status === 'number' ? error.status : undefined;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString('en-US', { maximumFractionDigits: 5 }) : String(value);
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function MetricsBlock({ title, data, testId }: { title: string; data: unknown; testId: string }) {
  const entries = isRecord(data)
    ? Object.entries(data).filter(([key]) =>
      key !== 'digest' && key !== 'signals_by_month' && key !== 'raw_by_timeframe')
    : [];
  if (!entries.length) return <div className="evolve-metric-empty">{data == null ? 'No simulation summary returned.' : displayValue(data)}</div>;
  return <section className="evolve-metrics" aria-label={title} data-testid={testId}>
    {entries.map(([key, value]) => <div className="evolve-metric" key={key}>
      <span>{key.replace(/[_-]/g, ' ').toUpperCase()}</span>
      <strong>{displayValue(value)}</strong>
    </div>)}
  </section>;
}

function ValidationComparison({ result }: { result: NonNullable<EvolveJobStatus['result']> }) {
  const comparison = isRecord(result.comparison) ? result.comparison : null;
  const candidateValidation = isRecord(result.candidate_validation)
    ? result.candidate_validation
    : null;
  const resultRecord = isRecord(result) ? result : {};
  const objective = resultRecord.objective === 'develop_better_filter'
    ? 'develop_better_filter'
    : 'maximize_profit';
  const rows: Array<{ gate: string; observed: unknown; required: unknown; passed: boolean }> = [];

  const floors = comparison && isRecord(comparison.validation_trade_floors)
    ? comparison.validation_trade_floors
    : {};
  Object.entries(floors).forEach(([timeframe, value]) => {
    if (!isRecord(value)) return;
    rows.push({
      gate: `Validation trade floor · ${timeframe}`,
      observed: value.closed_trade_count,
      required: `≥ ${displayValue(value.required)}`,
      passed: value.passed === true,
    });
  });

  if (comparison) {
    if (objective === 'develop_better_filter') {
      rows.push({
        gate: 'Filter objective improvement',
        observed: comparison.candidate_filter_objective_score,
        required: `> ${displayValue(comparison.champion_filter_objective_score)}`,
        passed: comparison.filter_objective_improved === true,
      });
      rows.push({
        gate: 'Signal retention',
        observed: typeof comparison.candidate_filter_retention === 'number'
          ? `${(comparison.candidate_filter_retention * 100).toFixed(1)}%`
          : comparison.candidate_filter_retention,
        required: '≥ 40%',
        passed: comparison.filter_retention_passed === true,
      });
      rows.push({
        gate: 'Positive filtered expectancy',
        observed: comparison.candidate_validation_filtered_expectancy_after_costs,
        required: '> 0 R',
        passed: comparison.filtered_expectancy_positive === true,
      });
    } else {
      rows.push({
        gate: 'Validation expectancy margin',
        observed: candidateValidation?.expectancy_after_costs,
        required: comparison.required_validation_expectancy_after_costs,
        passed: comparison.expectancy_margin_passed === true,
      });
      rows.push({
        gate: 'Positive expectancy',
        observed: candidateValidation?.expectancy_after_costs,
        required: '> 0 R',
        passed: comparison.expectancy_positive === true,
      });
    }
    const coverage = isRecord(comparison.annual_signal_month_coverage)
      ? comparison.annual_signal_month_coverage
      : null;
    if (coverage) {
      rows.push({
        gate: 'Annual month coverage',
        observed: coverage.covered_count,
        required: `≥ ${displayValue(coverage.required_count)}`,
        passed: coverage.passed === true,
      });
    }
  }

  if (!rows.length) return <div className="evolve-metric-empty">No validation comparison returned.</div>;
  return <div className="evolve-validation-table-wrap">
    {comparison && <div className="evolve-model-labels" data-testid="evolve-model-labels">
      <p><strong>CHAMPION</strong> · {displayValue(comparison.champion_simulation_label)}</p>
      <p><strong>CANDIDATE</strong> · {displayValue(comparison.candidate_simulation_label)}</p>
      <p>SWING VERSION · {displayValue(comparison.champion_swing_version)} / {displayValue(comparison.candidate_swing_version)}</p>
    </div>}
    <table className="evolve-validation-table" aria-label="Validation gates" data-testid="evolve-validation-table">
      <thead><tr><th>GATE</th><th>OBSERVED</th><th>REQUIRED</th><th>VERDICT</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={`${row.gate}-${index}`} data-testid={`evolve-validation-gate-${index}`}>
        <th scope="row">{row.gate}</th>
        <td>{displayValue(row.observed)}</td>
        <td>{displayValue(row.required)}</td>
        <td className={row.passed ? 'is-pass' : 'is-fail'}>{row.passed ? 'PASS' : 'FAIL'}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function Paths({ result }: { result: NonNullable<EvolveJobStatus['result']> }) {
  const paths = [
    ['PROPOSAL', result.proposal_file],
    ['CANDIDATE', result.candidate_file],
    ['RESULT', result.result_file],
    ['ARCHIVED BASELINE', result.archived_baseline],
  ].filter((item): item is [string, string] => typeof item[1] === 'string' && item[1].length > 0);

  if (!paths.length) return null;
  return <section className="evolve-section">
    <h3>ARCHIVE RECORDS</h3>
    <dl className="evolve-path-list">
      {paths.map(([label, path]) => <div key={label}>
        <dt>{label}</dt><dd>{path}</dd>
      </div>)}
    </dl>
  </section>;
}

function ResultView({ job }: {
  job: EvolveJobStatus;
}) {
  const result = job.result;
  if (!result) return null;

  const proposal = isRecord(result.proposal) ? result.proposal : null;
  const reasonCodes = Array.isArray(result.reason_codes) ? result.reason_codes : [];
  const validation = result.comparison;
  const tokenUsage = result.token_usage;
  const eligible = result.eligible === true;
  const missionConfig = isRecord(result.mission_config)
    ? result.mission_config
    : job.mission_config;

  return <div className="evolve-result" data-testid="evolve-result">
    <div className="evolve-result-heading">
      <div>
        <span className="evolve-kicker">ROUND {typeof result.round === 'number' ? result.round : '—'} · RESULT</span>
        <h2>{displayValue(result.decision || job.phase)}</h2>
      </div>
      <span className={`evolve-verdict${eligible ? ' is-eligible' : ' is-ineligible'}`} data-testid="evolve-eligibility">
        {eligible ? 'ELIGIBLE' : 'NOT ELIGIBLE'}
      </span>
    </div>

    {result.reason && <p className="evolve-reason">{result.reason}</p>}

    {missionConfig && <section className="evolve-section">
      <h3>MISSION CONFIGURATION</h3>
      <MetricsBlock title="Mission configuration" data={missionConfig} testId="evolve-mission-config"/>
    </section>}

    <section className="evolve-section">
      <h3>PROPOSED CHANGE</h3>
      <pre className="evolve-code" data-testid="evolve-proposed-diff">{displayValue(proposal?.diff)}</pre>
      <div className="evolve-proposal-copy">
        <div><span>RATIONALE</span><p>{displayValue(proposal?.rationale)}</p></div>
        <div><span>EXPECTED EFFECT</span><p>{displayValue(proposal?.expected_effect)}</p></div>
        {proposal?.no_change !== undefined && <div><span>NO-CHANGE PROPOSAL</span><p>{displayValue(proposal.no_change)}</p></div>}
      </div>
    </section>

    <section className="evolve-section">
      <h3>TRAINING SIMULATION</h3>
      <div className="evolve-simulation-grid">
        <div><h4>CHAMPION</h4><MetricsBlock title="Champion training metrics" data={result.champion_training} testId="evolve-champion-training"/></div>
        <div><h4>CANDIDATE</h4><MetricsBlock title="Candidate training metrics" data={result.candidate_training} testId="evolve-candidate-training"/></div>
      </div>
    </section>

    <section className="evolve-section">
      <h3>VALIDATION COMPARISON</h3>
      <div className="evolve-simulation-grid">
        <div><h4>CHAMPION · VALIDATION</h4><MetricsBlock title="Champion validation metrics" data={result.champion_validation} testId="evolve-champion-validation"/></div>
        <div><h4>CANDIDATE · VALIDATION</h4><MetricsBlock title="Candidate validation metrics" data={result.candidate_validation} testId="evolve-candidate-validation"/></div>
      </div>
      <ValidationComparison result={result}/>
      <div className="evolve-reason-block">
        <span>PASS / FAIL REASONS · SERVER REPORTED</span>
        {reasonCodes.length ? <ul>{reasonCodes.map((code, index) => <li key={`${String(code)}-${index}`}>{displayValue(code)}</li>)}</ul>
          : <p>No validation reason codes returned.</p>}
      </div>
    </section>

    <section className="evolve-section evolve-token-section">
      <h3>MODEL USAGE</h3>
      {isRecord(tokenUsage) && Object.keys(tokenUsage).length
        ? <div className="evolve-token-grid">{Object.entries(tokenUsage).map(([key, value]) => <div key={key}>
            <span>{key.replace(/[_-]/g, ' ').toUpperCase()}</span><strong>{displayValue(value)}</strong>
          </div>)}</div>
        : <p className="evolve-muted">Token usage not reported.</p>}
    </section>

    <Paths result={result}/>
  </div>;
}

export default function EvolveControl({
  approvedSymbols,
}: {
  approvedSymbols: InstrumentSymbol[];
}) {
  const [jobId, setJobId] = useState<string | null>(() => readPersistedState().jobId);
  const [missionConfig, setMissionConfig] = useState<EvolveMissionConfig>(
    () => readPersistedMissionConfig(),
  );
  const [localStatus, setLocalStatus] = useState<EvolveJobStatus | null>(null);
  const [confirmPromote, setConfirmPromote] = useState(false);
  const [showPreflight, setShowPreflight] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const runMutation = useRunEvolveRound();
  const decideMutation = useDecideEvolveJob();
  const statusQuery = useGetEvolveStatus(jobId ?? '', {
    query: {
      enabled: Boolean(jobId),
      queryKey: getGetEvolveStatusQueryKey(jobId ?? ''),
      refetchInterval: query => {
        if (errorStatus(query.state.error) === 404) return false;
        const phase = query.state.data?.phase;
        return phase === 'complete' || phase === 'failed' ? false : 1800;
      },
    },
  });
  const staleJob = Boolean(statusQuery.isError && errorStatus(statusQuery.error) === 404);

  const status = useMemo(() => {
    if (staleJob) return null;
    const queried = statusQuery.data;
    if (localStatus?.job_id === jobId && (!queried || localStatus.updated_at >= queried.updated_at)) return localStatus;
    return queried?.job_id === jobId ? queried : null;
  }, [jobId, localStatus, staleJob, statusQuery.data]);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ jobId }));
    } catch {
      // The control remains usable if browser storage is unavailable.
    }
  }, [jobId]);

  useEffect(() => {
    try {
      window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(missionConfig));
    } catch {
      // The configuration remains available for the current session.
    }
  }, [missionConfig]);

  useEffect(() => {
    if (status) setLocalStatus(current => current?.job_id === status.job_id && current.updated_at > status.updated_at ? current : status);
  }, [status]);

  const active = Boolean(status && status.phase !== 'complete' && status.phase !== 'failed');
  const statusStillLoading = Boolean(jobId && statusQuery.isLoading && !status);
  const canOpenPreflight = !active && !statusStillLoading
    && (!jobId || Boolean(status) || staleJob)
    && !runMutation.isPending && !decideMutation.isPending;
  const canRun = approvedSymbols.includes(missionConfig.symbol) && canOpenPreflight;
  const missionError = missionConfigError(missionConfig);

  const startRound = () => {
    if (!canRun || missionError) return;
    setActionError(null);
    runMutation.mutate({ data: { missionConfig } }, {
      onSuccess: result => {
        setJobId(result.job_id);
        setLocalStatus(result);
        setShowPreflight(false);
      },
      onError: error => setActionError(error instanceof Error ? error.message : 'Unable to start the evolution round.'),
    });
  };

  const submitDecision = (promote: boolean) => {
    if (!jobId) return;
    setActionError(null);
    decideMutation.mutate({ data: { job_id: jobId, promote } }, {
      onSuccess: result => {
        setLocalStatus(result);
        setConfirmPromote(false);
      },
      onError: error => setActionError(error instanceof Error ? error.message : 'Unable to submit owner decision.'),
    });
  };

  const logs = status?.logs ?? [];
  const result = status?.result;
  const showDecisionButtons = status?.phase === 'awaiting-owner-decision' && result?.eligible === true;
  const statusError = statusQuery.isError
    ? staleJob
      ? 'This job is no longer in server memory. Completed results remain available in Round History.'
      : statusQuery.error instanceof Error ? statusQuery.error.message : 'Could not retrieve evolution status.'
    : null;
  const historyRefreshKey = status
    && (status.phase === 'complete' || status.phase === 'failed')
    ? `${status.job_id}:${status.phase}:${status.updated_at}`
    : null;
  const clearJobState = () => {
    setJobId(null);
    setLocalStatus(null);
    setActionError(null);
    setConfirmPromote(false);
  };

  return <>
    <section className="evolve-control" aria-label="Evolution round control">
    <div className="evolve-bar">
      <div className="evolve-bar-copy">
        <span className="evolve-bar-mark" aria-hidden="true">EV</span>
        <div>
          <strong>EVOLUTION ROUND</strong>
          <small>FROZEN STRATEGY · HISTORICAL {missionConfig.symbol} · SWING {missionConfig.swingVersion} · RESEARCH ONLY</small>
        </div>
      </div>
      <div className="evolve-bar-state" data-testid="evolve-job-state" role="status">
        {status ? <><i className={active ? 'is-active' : status.phase === 'failed' ? 'is-failed' : 'is-done'}/>{status.phase.toUpperCase()}</>
          : runMutation.isPending ? 'SUBMITTING ROUND…' : 'NO ROUND LOADED'}
      </div>
      <button type="button" className="evolve-button evolve-run-button" onClick={() => setShowPreflight(true)} disabled={!canOpenPreflight} data-testid="button-run-evolve-round">
        {runMutation.isPending ? 'STARTING…' : 'RUN EVOLVE ROUND'}
      </button>
    </div>

    {runMutation.isPending && <div className="evolve-loading" role="status" data-testid="evolve-loading">
      <span/><span/><span/> Submitting a research round…
    </div>}

    {(statusError || actionError || runMutation.isError || decideMutation.isError) && <div className="evolve-error" role="alert" data-testid="evolve-api-error">
      {actionError ?? statusError ?? 'The request could not be completed.'}
      {staleJob && jobId
        ? <button type="button" onClick={clearJobState} data-testid="button-clear-stale-evolve-job">CLEAR STALE JOB</button>
        : statusError && jobId && <button type="button" onClick={() => void statusQuery.refetch()} data-testid="button-retry-evolve-status">RETRY STATUS</button>}
    </div>}

    {jobId && statusStillLoading && <div className="evolve-skeleton" aria-label="Loading evolution status" data-testid="evolve-status-loading"><i/><i/><i/></div>}
    {!jobId && <div className="evolve-empty-state" data-testid="evolve-empty-state">
      <span>NO JOB RESTORED</span>
      <p>Start a research round to review a frozen strategy against the approved historical XAUUSD record.</p>
    </div>}

    {status && <div className="evolve-detail">
      <div className="evolve-meta-line">
        <span>JOB <b>{status.job_id}</b></span>
        <span>SCOPE <b>{status.symbol}</b></span>
        <span>UPDATED <b>{status.updated_at ? new Date(status.updated_at).toLocaleString('en-GB', { timeZone: 'UTC', hour12: false }) + ' UTC' : '—'}</b></span>
        {active && <span className="evolve-polling" data-testid="evolve-polling-indicator">POLLING STATUS</span>}
      </div>

      <section className="evolve-section evolve-log-section">
        <h3>ROUND JOURNAL <span>{logs.length.toString().padStart(2, '0')} ENTRIES</span></h3>
        {logs.length ? <>
          <div className="evolve-log-columns" aria-hidden="true">
            <span>TIME · UTC</span><span>PHASE</span><span>EVENT</span>
          </div>
          <ol className="evolve-log-list" aria-label="Evolution round log" data-testid="evolve-log-lines">
            {logs.map((line, index) => <li key={`${line.at}-${index}`} data-testid={`evolve-log-line-${index}`}>
              <time><span className="evolve-mobile-label">TIME · UTC</span>{line.at}</time>
              <b><span className="evolve-mobile-label">PHASE</span>{line.phase.toUpperCase()}</b>
              <span><span className="evolve-mobile-label">EVENT</span>{line.message}</span>
            </li>)}
          </ol>
        </> : <div className="evolve-empty" data-testid="evolve-empty-logs">Waiting for the first round log entry.</div>}
      </section>

      {status.phase === 'failed' && <div className="evolve-failure" role="alert" data-testid="evolve-failed-state">
        <strong>ROUND FAILED</strong><span>{status.error || 'The job ended without a result.'}</span>
        <button type="button" className="evolve-failure-dismiss" onClick={clearJobState} aria-label="Dismiss failure panel" data-testid="button-dismiss-evolve-failure">DISMISS</button>
      </div>}

      {result && <ResultView job={status}/>}

      {showDecisionButtons && <div className="evolve-decision-row" data-testid="evolve-owner-decision">
        <div><strong>OWNER DECISION REQUIRED</strong><span>Eligibility is reported by the server. Review the validation record before deciding.</span></div>
        <button type="button" className="evolve-button evolve-reject-button" onClick={() => submitDecision(false)} disabled={decideMutation.isPending} data-testid="button-reject-evolve-candidate">
          {decideMutation.isPending ? 'SUBMITTING…' : 'REJECT'}
        </button>
        <button type="button" className="evolve-button evolve-promote-button" onClick={() => setConfirmPromote(true)} disabled={decideMutation.isPending} data-testid="button-promote-evolve-candidate">
          PROMOTE
        </button>
      </div>}
    </div>}

    {confirmPromote && <div className="evolve-dialog-backdrop" role="presentation">
      <div className="evolve-dialog" role="dialog" aria-modal="true" aria-labelledby="evolve-confirm-title" aria-describedby="evolve-confirm-copy" data-testid="dialog-confirm-evolve-promotion">
        <span className="evolve-kicker">OWNER CONFIRMATION</span>
        <h2 id="evolve-confirm-title">Promote this candidate?</h2>
        <p id="evolve-confirm-copy">The champion change is versioned and rollback-able. Promotion updates the research baseline only; it does not place trades.</p>
        <div className="evolve-dialog-actions">
          <button type="button" className="evolve-button evolve-button-secondary" onClick={() => setConfirmPromote(false)} disabled={decideMutation.isPending} data-testid="button-cancel-evolve-promotion">CANCEL</button>
          <button type="button" className="evolve-button evolve-promote-button" onClick={() => submitDecision(true)} disabled={decideMutation.isPending} data-testid="button-confirm-evolve-promotion">
            {decideMutation.isPending ? 'SUBMITTING…' : 'CONFIRM PROMOTION'}
          </button>
        </div>
      </div>
    </div>}

    {showPreflight && <div className="evolve-dialog-backdrop" role="presentation">
      <div
        className="evolve-dialog evolve-preflight-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="evolve-preflight-title"
        data-testid="dialog-evolve-preflight"
      >
        <div className="evolve-preflight-heading">
          <div>
            <span className="evolve-kicker">ROUND CONFIGURATION</span>
            <h2 id="evolve-preflight-title">Review the mission before starting</h2>
          </div>
          <button
            type="button"
            className="evolve-button evolve-button-secondary"
            onClick={() => setMissionConfig(DEFAULT_MISSION_CONFIG)}
            data-testid="button-evolve-defaults"
          >
            DEFAULT
          </button>
        </div>

        <div className="evolve-preflight-grid">
          <label className="evolve-config-field">
            <span>SYMBOL</span>
            <select
              value={missionConfig.symbol}
              onChange={event => setMissionConfig(config => {
                const selectedSymbol = event.target.value as EvolveMissionConfig['symbol'];
                return {
                  ...config,
                  symbol: selectedSymbol,
                  directionMode: selectedSymbol === 'XAUUSD' ? config.directionMode : 'normal',
                  swingVersion: selectedSymbol === 'XAUUSD' ? config.swingVersion : 'V1',
                };
              })}
              data-testid="evolve-config-symbol"
            >
              {MISSION_SYMBOLS.map(value => <option value={value} key={value}>{value}</option>)}
            </select>
          </label>
          <fieldset className="evolve-config-field">
            <legend>TRAINING YEARS</legend>
            <div className="evolve-year-options">
              {MISSION_YEARS.map(year => <label key={`training-${year}`}>
                <input
                  type="checkbox"
                  checked={missionConfig.trainingYears.includes(year)}
                  data-testid={`training-year-${year}`}
                  onChange={() => setMissionConfig(config => ({
                    ...config,
                    trainingYears: toggleYear(config.trainingYears, year),
                  }))}
                />
                <span>{year}</span>
              </label>)}
            </div>
          </fieldset>
          <fieldset className="evolve-config-field">
            <legend>VALIDATION YEARS</legend>
            <div className="evolve-year-options">
              {MISSION_YEARS.map(year => <label key={`validation-${year}`}>
                <input
                  type="checkbox"
                  checked={missionConfig.validationYears.includes(year)}
                  data-testid={`validation-year-${year}`}
                  onChange={() => setMissionConfig(config => ({
                    ...config,
                    validationYears: toggleYear(config.validationYears, year),
                  }))}
                />
                <span>{year}</span>
              </label>)}
            </div>
          </fieldset>
          <label className="evolve-config-field">
            <span>EXIT MODEL</span>
            <select
              value={missionConfig.exitModel}
              onChange={event => setMissionConfig(config => ({
                ...config,
                exitModel: event.target.value as EvolveMissionConfig['exitModel'],
              }))}
            >
              <option value="swept_level_atr">Swept level ATR</option>
              <option value="candle_geometry">Candle geometry</option>
            </select>
          </label>
          <label className="evolve-config-field">
            <span>DIRECTION</span>
            <select
              value={missionConfig.directionMode}
              disabled={missionConfig.symbol !== 'XAUUSD'}
              onChange={event => setMissionConfig(config => ({
                ...config,
                directionMode: event.target.value as EvolveMissionConfig['directionMode'],
              }))}
            >
              <option value="normal">Normal</option>
              <option value="inverted">Inverted</option>
              <option value="adaptive">Adaptive</option>
            </select>
          </label>
          <label className="evolve-config-field">
            <span>SWING VERSION · PINNED FOR THIS ROUND</span>
            <select
              value={missionConfig.swingVersion}
              onChange={event => setMissionConfig(config => ({
                ...config,
                swingVersion: event.target.value as EvolveMissionConfig['swingVersion'],
              }))}
            >
              <option value="V1">V1</option>
              <option value="V2" disabled={missionConfig.symbol !== 'XAUUSD'}>V2</option>
            </select>
          </label>
          <fieldset className="evolve-config-field">
            <legend>MODEL PROVIDERS · AT LEAST ONE</legend>
            <div className="evolve-provider-options">
              {(['GEMINI', 'GROQ'] as const).map(provider => <label key={provider}>
                <input
                  type="checkbox"
                  checked={missionConfig.providers.includes(provider)}
                  data-testid={`provider-${provider.toLowerCase()}`}
                  onChange={() => setMissionConfig(config => ({
                    ...config,
                    providers: config.providers.includes(provider)
                      ? config.providers.filter(value => value !== provider)
                      : [...config.providers, provider],
                  }))}
                />
                <span>{provider}</span>
              </label>)}
            </div>
          </fieldset>
          <label className="evolve-config-field">
            <span>OBJECTIVE</span>
            <select
              value={missionConfig.objective}
              onChange={event => setMissionConfig(config => ({
                ...config,
                objective: event.target.value as EvolveMissionConfig['objective'],
              }))}
            >
              <option value="maximize_profit">Maximize profit · validation expectancy</option>
              <option value="develop_better_filter">Develop better filter · raw − filtered expectancy</option>
            </select>
          </label>
        </div>

        <p className="evolve-config-note">
          Promotion still requires at least 100 validation trades per active timeframe. Short windows can fail that floor.
        </p>
        {missionError && <p className="evolve-config-error" role="alert" data-testid="evolve-config-error">{missionError}</p>}
        {!approvedSymbols.includes(missionConfig.symbol) && <p className="evolve-config-error" role="alert">{missionConfig.symbol} is not marked as approved for evolution.</p>}
        <div className="evolve-dialog-actions">
          <button
            type="button"
            className="evolve-button evolve-button-secondary"
            onClick={() => setShowPreflight(false)}
            disabled={runMutation.isPending}
            data-testid="button-cancel-evolve-preflight"
          >
            CANCEL
          </button>
          <button
            type="button"
            className="evolve-button evolve-run-button"
            onClick={startRound}
            disabled={!canRun || Boolean(missionError)}
            data-testid="button-confirm-evolve-round"
          >
            {runMutation.isPending ? 'STARTING…' : 'CONFIRM AND RUN'}
          </button>
        </div>
      </div>
    </div>}
    </section>
    <EvolveRoundHistory refreshKey={historyRefreshKey}/>
  </>;
}
