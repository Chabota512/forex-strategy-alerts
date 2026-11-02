import { getGetSystemIntelligenceQueryKey, useGetSystemIntelligence } from '@workspace/api-client-react';
import type { SystemIntelligence } from '@workspace/api-client-react';
import { useEffect, useState } from 'react';
import { AlertTriangle, ChevronDown, RefreshCw } from 'lucide-react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { getSiFailureCategory, notifySiFailure } from '@/lib/si-failure-toast';
import './system-intelligence.css';

const pct = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(1)}%`;
const number = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? '—' : value.toFixed(2);
const expectancyText = (r: number | null | undefined, money: number | null | undefined) => {
  const rText = r == null || !Number.isFinite(r) ? '—' : `${r.toFixed(2)}R`;
  const moneyText = money == null || !Number.isFinite(money)
    ? '—'
    : `${money < 0 ? '−' : '+'}$${Math.abs(money).toFixed(2)}`;
  return `${rText} · ${moneyText}/trade`;
};
const dateLabel = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', timeZone: 'UTC' }).format(date);
};
const dateTime = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : `${new Intl.DateTimeFormat('en-GB', {
        year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
        hour12: false, timeZone: 'UTC',
      }).format(date)} UTC`;
};
const evidenceValue = (value: unknown): string => {
  if (value == null) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try { return JSON.stringify(value); } catch { return String(value); }
};
const label = (value: string) => value.replace(/[_-]/g, ' ').toUpperCase();

type ProposedRule = {
  id: string;
  name: string;
  description: string;
  rationale: string;
  condition: Record<string, unknown>;
};

type ProposalCluster = {
  condition?: { all?: Array<{ field?: string; value?: unknown }> };
  sampleCount?: number;
  hitRate?: number;
  expectancyR?: number;
  fadeVsFollow?: {
    pairedAlerts?: number;
    meanFadeMinusFollowR?: number | null;
  };
};

type ProposalEvidence = {
  resolvedCaseCount?: number;
  minimumClusterSize?: number;
  clusters?: ProposalCluster[];
  interpretation?: string;
};

type ProposalAttempt = {
  status: string;
  reason?: string;
  provider?: string | null;
  key_index?: number | null;
  token_usage?: { input?: number | null; output?: number | null; total?: number | null } | null;
};

type ProposalSummary = {
  threshold: number;
  newResolvedCases: number;
  eligible: boolean;
  generating: boolean;
  pending: {
    proposal_id: string;
    candidate: ProposedRule;
    evidence?: ProposalEvidence;
    status: string;
  } | null;
  latestAttempt: ProposalAttempt | null;
  attemptCount: number;
  tokenUsage: { input: number | null; output: number | null; total: number | null };
};

type IntelligenceWithProposal = SystemIntelligence & { proposal?: ProposalSummary };

async function postIntelligenceAction(path: string, payload: Record<string, string> = {}) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  let result: { error?: string } = {};
  try {
    result = await response.json() as { error?: string };
  } catch {
    // Keep the HTTP status as the useful failure detail for an empty response.
  }
  if (!response.ok) {
    throw new Error(result.error ?? `Request failed (${response.status}).`);
  }
  return result;
}

function Evidence({ evidence }: { evidence: SystemIntelligence['lessons'][number]['evidence'] }) {
  const entries = Object.entries(evidence);
  if (!entries.length) return <div className="intel-evidence"><span>No evidence fields supplied</span></div>;
  return <div className="intel-evidence" aria-label="Lesson evidence">
    {entries.map(([key, value]) => <span key={key}><b>{label(key)}:</b> {evidenceValue(value)}</span>)}
  </div>;
}

function TunableGroup({ title, values }: { title: string; values: Record<string, number> }) {
  const entries = Object.entries(values);
  return <section className="intel-tunable-group">
    <h4>{title}</h4>
    {entries.length ? entries.map(([key, value]) => <div className="intel-tunable-row" key={key}>
      <span>{label(key)}</span><strong>{number(value)}</strong>
    </div>) : <div className="intel-tunable-row"><span>No values reported</span><strong>—</strong></div>}
  </section>;
}

function IntelligenceChart({ data }: { data: SystemIntelligence['hitRateOverTime'] }) {
  return <div className="intel-chart-wrap" role="img" aria-label="XAUUSD hit rate over time">
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 9, left: -18, bottom: 0 }}>
        <CartesianGrid stroke="var(--grid)" strokeDasharray="2 5" vertical={false}/>
        <XAxis dataKey="at" tickFormatter={dateLabel} tick={{ fill: 'var(--text-muted)', fontSize: 8, fontFamily: 'var(--app-font-mono)' }} axisLine={{ stroke: 'var(--line)' }} tickLine={false} minTickGap={22}/>
        <YAxis domain={[0, 100]} tickFormatter={value => `${value}%`} tick={{ fill: 'var(--text-muted)', fontSize: 8, fontFamily: 'var(--app-font-mono)' }} axisLine={false} tickLine={false} width={42}/>
        <Tooltip
          content={({ active, payload, label: at }) => active && payload?.length ? <div className="intel-tooltip">
            <time>{dateTime(String(at ?? ''))}</time>
            <strong>HIT RATE · {pct(Number(payload[0]?.value))}</strong>
            <strong>CASES · {Number(payload[0]?.payload.caseCount ?? 0).toLocaleString()}</strong>
            <strong>EXPECTANCY · {expectancyText(
              Number(payload[0]?.payload.expectancy),
              Number(payload[0]?.payload.expectancyMoney),
            )}</strong>
          </div> : null}
        />
        <Line type="monotone" dataKey="hitRate" stroke="var(--accent)" strokeWidth={2} dot={{ r: 2.5, fill: 'var(--surface)', stroke: 'var(--accent)', strokeWidth: 1.5 }} activeDot={{ r: 4, fill: 'var(--accent)' }} isAnimationActive={false}/>
      </LineChart>
    </ResponsiveContainer>
  </div>;
}

function LoadedIntelligence({ data, refresh }: {
  data: IntelligenceWithProposal;
  refresh: () => Promise<unknown>;
}) {
  const expectancyClass = data.overall.expectancy == null ? '' : data.overall.expectancy >= 0 ? 'is-positive' : 'is-negative';
  const [rejectReason, setRejectReason] = useState('');
  const [actionError, setActionError] = useState('');
  const [actionPending, setActionPending] = useState(false);
  const proposal = data.proposal;
  const pending = proposal?.pending ?? null;
  const performAction = async (path: string, payload: Record<string, string> = {}) => {
    setActionPending(true);
    setActionError('');
    try {
      await postIntelligenceAction(path, payload);
      setRejectReason('');
      await refresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'The proposal action failed.');
    } finally {
      setActionPending(false);
    }
  };
  return <>
    <div className="intel-overview" data-testid="intel-overview">
      <div className="intel-overall">
        <span className="intel-overall-label">ALL OBSERVED CASES · XAUUSD</span>
        <div className="intel-overall-value"><strong>{pct(data.overall.hitRate)}</strong><span>hit rate</span></div>
        <div className="intel-overall-foot">{data.overall.hits.toLocaleString()} hits / {data.overall.misses.toLocaleString()} misses</div>
      </div>
      <div className="intel-stat"><span>CASE COUNT</span><strong>{data.caseCount.toLocaleString()}</strong></div>
      <div className="intel-stat"><span>EXPECTANCY</span><strong className={expectancyClass}>{expectancyText(data.overall.expectancy, data.overall.expectancyMoney)}</strong></div>
      <div className="intel-stat"><span>ACTIVE RULES</span><strong>{data.rules.length.toString().padStart(2, '0')}</strong></div>
    </div>
    <p className="intel-capital-context" data-testid="intel-capital-context">
      CAPITAL · ${data.overall.capitalContext.balance_usd.toLocaleString('en-US')} · {data.overall.capitalContext.sizing}
      {Object.entries(data.overall.capitalContext.lots).map(([symbol, lots]) => ` · ${lots} ${symbol}`).join('')}
    </p>
    <section className="intel-section" aria-labelledby="intel-history-title">
      <div className="intel-section-head"><h3 id="intel-history-title">HIT RATE · OVER TIME</h3><span>{data.hitRateOverTime.length} observations</span></div>
      {data.hitRateOverTime.length ? <IntelligenceChart data={data.hitRateOverTime}/> :
        <div className="intel-message" data-testid="intel-chart-empty"><div><strong>NO HISTORY TO PLOT</strong><p>No hit-rate observations are available yet.</p></div></div>}
    </section>
    <section className="intel-section" aria-labelledby="intel-rules-title">
        <div className="intel-section-head"><h3 id="intel-rules-title">ACTIVE BELIEF RULES</h3><span>Observed evidence · not a live decision system</span></div>
      {data.rules.length ? <div className="intel-rule-list" data-testid="intel-rules">
        {data.rules.map(rule => <article className="intel-rule" key={rule.id} data-testid={`intel-rule-${rule.id}`}>
          <div className="intel-rule-top"><h4>{rule.name}</h4>{rule.custom && <span className="intel-rule-flag">OWNER APPROVED</span>}{rule.flagged && <span className="intel-rule-flag">FLAGGED</span>}</div>
          <p>{rule.description}</p>
          <div className="intel-rule-metrics">
            <div><span>SAMPLES</span><strong>{rule.samples.toLocaleString()}</strong></div>
            <div><span>HIT RATE</span><strong>{pct(rule.hitRate)}</strong></div>
            <div><span>EXPECTANCY</span><strong>{expectancyText(rule.expectancy, rule.expectancyMoney)}</strong></div>
            <div><span>BASELINE</span><strong>{pct(rule.baselineHitRate)}</strong></div>
          </div>
          <div className="intel-rule-weight"><span>RULE WEIGHT</span><strong>{number(rule.weight)}</strong></div>
        </article>)}
      </div> : <div className="intel-message" data-testid="intel-rules-empty"><div><strong>NO SEEDED RULES RETURNED</strong><p>Rule-level evidence will appear when available.</p></div></div>}
    </section>
    <section className="intel-section" aria-labelledby="intel-lessons-title">
      <div className="intel-section-head"><h3 id="intel-lessons-title">LESSON LEDGER</h3><span>{data.lessons.length} evidence records</span></div>
      {data.lessons.length ? <div className="intel-ledger" data-testid="intel-lessons" tabIndex={0} aria-label="Scrollable lesson ledger" style={{ maxHeight: 320, overflowY: 'auto' }}>
        {[...data.lessons].reverse().map(lesson => <article className="intel-lesson" key={lesson.id} data-testid={`intel-lesson-${lesson.id}`}>
          <div className="intel-lesson-meta"><strong>{lesson.ruleName}</strong><time dateTime={lesson.createdAtUtc}>{dateTime(lesson.createdAtUtc)}</time></div>
          <div className="intel-lesson-body"><div className="intel-lesson-action">{lesson.action}</div><Evidence evidence={lesson.evidence}/></div>
        </article>)}
      </div> : <div className="intel-message" data-testid="intel-lessons-empty"><div><strong>LEDGER IS EMPTY</strong><p>No evidence-backed lessons have been recorded.</p></div></div>}
    </section>
    <section className="intel-section" aria-labelledby="intel-tunables-title">
      <div className="intel-section-head"><h3 id="intel-tunables-title">CURRENT BOUNDED TUNABLES</h3><span>Read only · server-reported values</span></div>
      <div className="intel-tunable-groups" data-testid="intel-tunables">
        <TunableGroup title="STRENGTH WEIGHTS" values={data.tunables.strengthWeights}/>
        <TunableGroup title="STRENGTH THRESHOLDS" values={data.tunables.strengthThresholds}/>
        <TunableGroup title="INVERSION WEIGHTS" values={data.tunables.invertWeights}/>
      </div>
      <p className="intel-bounded-note">These values are displayed for research context only. Their bounds and any changes are governed by the server; this panel cannot edit or apply them.</p>
    </section>
    <section className="intel-section" aria-labelledby="intel-suggestion-title">
      <div className="intel-section-head"><h3 id="intel-suggestion-title">OWNER REVIEW QUEUE</h3><span>NEVER AUTO-ACCEPTED</span></div>
      <div className="intel-message" data-testid="intel-accept-suggestion-slot">
        {pending ? <>
          <div className="intel-proposal-copy">
            <strong>{pending.candidate.name}</strong>
            <p>{pending.candidate.rationale}</p>
            <code>{JSON.stringify(pending.candidate.condition)}</code>
            <p>Based on aggregate-only evidence. Approval starts this rule at low confidence and collects new cases only.</p>
            <div className="intel-proposal-evidence" data-testid="intel-proposal-evidence">
              <div className="intel-proposal-evidence-head">
                <strong>CLUSTER-STAT EVIDENCE</strong>
                <span>{pending.evidence?.resolvedCaseCount ?? 0} RESOLVED CASES · MIN CLUSTER {pending.evidence?.minimumClusterSize ?? '—'}</span>
              </div>
              {(pending.evidence?.clusters ?? []).length > 0
                ? pending.evidence?.clusters?.map((cluster, index) => {
                  const clause = cluster.condition?.all?.[0];
                  const delta = cluster.fadeVsFollow?.meanFadeMinusFollowR;
                  return <div className="intel-proposal-cluster" key={`${clause?.field ?? 'cluster'}-${index}`}>
                    <strong>{label(clause?.field ?? 'cluster')} = {evidenceValue(clause?.value)}</strong>
                    <span>n {cluster.sampleCount ?? '—'}</span>
                    <span>HIT {cluster.hitRate == null ? '—' : `${(cluster.hitRate * 100).toFixed(1)}%`}</span>
                    <span>E[R] {cluster.expectancyR == null ? '—' : `${cluster.expectancyR.toFixed(2)}R`}</span>
                    {delta != null && <span>FADE−FOLLOW {delta >= 0 ? '+' : ''}{delta.toFixed(2)}R · {cluster.fadeVsFollow?.pairedAlerts ?? 0} PAIRS</span>}
                  </div>;
                })
                : <p>No matching clusters met the minimum sample size.</p>}
              <p>{pending.evidence?.interpretation ?? 'Single-feature aggregate statistics; not a joint rule backtest.'}</p>
            </div>
          </div>
          <div className="intel-proposal-actions">
            <button
              type="button"
              disabled={actionPending}
              onClick={() => void performAction(
                `/api/evolve/system-intelligence/proposals/${encodeURIComponent(pending.proposal_id)}/accept`,
              )}
              aria-label="Accept suggestion"
              data-testid="button-accept-intel-suggestion"
            >{actionPending ? 'SAVING…' : 'ACCEPT SUGGESTION'}</button>
            <label className="intel-reject-reason">
              <span>REJECTION REASON</span>
              <input
                value={rejectReason}
                maxLength={300}
                onChange={event => setRejectReason(event.target.value)}
                placeholder="Why should this rule be archived?"
                aria-label="Rejection reason"
                data-testid="input-intel-rejection-reason"
              />
            </label>
            <button
              type="button"
              disabled={actionPending || !rejectReason.trim()}
              onClick={() => void performAction(
                `/api/evolve/system-intelligence/proposals/${encodeURIComponent(pending.proposal_id)}/reject`,
                { reason: rejectReason },
              )}
              data-testid="button-reject-intel-suggestion"
            >REJECT &amp; ARCHIVE</button>
          </div>
        </> : <>
          <div>
            <strong>{proposal?.generating ? 'PROPOSAL IN PROGRESS' : 'NO PENDING RULE'}</strong>
            <p>{proposal?.eligible
              ? 'Enough new resolved cases are available for an aggregate-only Gemini proposal.'
              : `${proposal?.newResolvedCases ?? 0} / ${proposal?.threshold ?? 50} new resolved cases since the last proposal attempt.`}
            </p>
            {proposal?.latestAttempt && <p data-testid="intel-proposal-last-attempt">
              LAST ATTEMPT · {proposal.latestAttempt.status.toUpperCase()}
              {proposal.latestAttempt.reason ? ` · ${proposal.latestAttempt.reason}` : ''}
            </p>}
          </div>
          <button
            type="button"
            disabled={!proposal?.eligible || actionPending || proposal.generating}
            onClick={() => void performAction('/api/evolve/system-intelligence/propose')}
            aria-label="Generate rule proposal"
            data-testid="button-generate-intel-suggestion"
          >{actionPending || proposal?.generating ? 'PROPOSING…' : 'PROPOSE RULE'}</button>
          <button
            type="button"
            disabled
            aria-label="Accept suggestion (no pending rule)"
            data-testid="button-accept-intel-suggestion"
          >ACCEPT SUGGESTION</button>
        </>}
        <span className="intel-proposal-usage" data-testid="intel-proposal-usage">
          ATTEMPTS · {proposal?.attemptCount ?? 0}
          {' · '}TOKENS · {proposal?.tokenUsage?.total ?? '—'}
        </span>
        {actionError && <p role="alert" className="intel-proposal-error" data-testid="intel-proposal-error">{actionError}</p>}
      </div>
    </section>
  </>;
}

export function SystemIntelligencePanel({ enabled = false }: { enabled?: boolean }) {
  const [collapsed, setCollapsed] = useState(false);
  const query = useGetSystemIntelligence({
    query: { queryKey: getGetSystemIntelligenceQueryKey(), refetchInterval: 30000 },
  });
  const data = query.data as IntelligenceWithProposal | undefined;
  useEffect(() => {
    if (query.isError) {
      notifySiFailure(getSiFailureCategory(query.error), enabled);
    }
  }, [enabled, query.error, query.isError]);
  return <section className="system-intelligence" aria-label="System intelligence" data-testid="system-intelligence">
    <header className="intel-heading">
      <button
        type="button"
        className="intel-heading-toggle"
        aria-expanded={!collapsed}
        aria-controls="system-intelligence-content"
        data-testid="toggle-system-intelligence"
        onClick={() => setCollapsed(current => !current)}
      >
        <span className="intel-title-group"><span className="intel-mark" aria-hidden="true">SI</span><span><span className="intel-kicker">XAUUSD · RESEARCH SURFACE</span><span className="intel-heading-title" role="heading" aria-level={2}>SYSTEM INTELLIGENCE</span></span></span>
        <span className="intel-live-note"><i aria-hidden="true"/> READ-ONLY · REFRESHES EVERY 30 SEC</span>
        <ChevronDown className={`intel-collapse-icon${collapsed ? ' is-collapsed' : ''}`} size={14} aria-hidden="true"/>
      </button>
    </header>
    <div id="system-intelligence-content" hidden={collapsed}>
      <div className="intel-disclaimer" role="note">
        <AlertTriangle size={13} aria-hidden="true"/>
        <strong>SIMULATION ONLY</strong>
        <span>Historical XAUUSD research only. No live recommendations or order execution; parameters cannot be applied from this panel.</span>
      </div>
      {query.isLoading && !data ? <div className="intel-skeleton" role="status" aria-label="Loading system intelligence" data-testid="intel-loading"><i/><i/><i/></div>
        : query.isError && !data ? <div className="intel-message" role="alert" data-testid="intel-error">
          <div><strong>INTELLIGENCE UNAVAILABLE</strong><p>{query.error instanceof Error ? query.error.message : 'The research summary could not be loaded.'}</p></div>
          <button type="button" onClick={() => void query.refetch()} data-testid="button-retry-intelligence"><RefreshCw size={11}/> RETRY</button>
        </div>
        : data ? <>
          {data.caseCount === 0 && <div className="intel-message" data-testid="intel-empty">
            <div><strong>NO XAUUSD CASES YET</strong><p>There is no historical sample to summarize. Seeded rules and bounded values remain visible below.</p></div>
          </div>}
          <LoadedIntelligence data={data} refresh={() => query.refetch()}/>
        </> : null}
    </div>
  </section>;
}

export default SystemIntelligencePanel;
