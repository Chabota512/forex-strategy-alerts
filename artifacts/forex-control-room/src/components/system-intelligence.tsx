import { getGetSystemIntelligenceQueryKey, useGetSystemIntelligence } from '@workspace/api-client-react';
import type { SystemIntelligence } from '@workspace/api-client-react';
import { useEffect } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
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

function LoadedIntelligence({ data }: { data: SystemIntelligence }) {
  const expectancyClass = data.overall.expectancy == null ? '' : data.overall.expectancy >= 0 ? 'is-positive' : 'is-negative';
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
      <div className="intel-section-head"><h3 id="intel-rules-title">SEEDED RULES</h3><span>Observed evidence · not a live decision system</span></div>
      {data.rules.length ? <div className="intel-rule-list" data-testid="intel-rules">
        {data.rules.map(rule => <article className="intel-rule" key={rule.id} data-testid={`intel-rule-${rule.id}`}>
          <div className="intel-rule-top"><h4>{rule.name}</h4>{rule.flagged && <span className="intel-rule-flag">FLAGGED</span>}</div>
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
      <div className="intel-section-head"><h3 id="intel-suggestion-title">ACCEPT SUGGESTION</h3><span>INERT SLOT · DISABLED</span></div>
      <div className="intel-message" data-testid="intel-accept-suggestion-slot">
        <div><strong>NO SUGGESTION CAN BE APPLIED</strong><p>Reserved for future owner review; this control cannot change the system.</p></div>
        <button
          type="button"
          disabled
          aria-label="Accept suggestion (inactive)"
          data-testid="button-accept-intel-suggestion"
        >ACCEPT SUGGESTION</button>
      </div>
    </section>
  </>;
}

export function SystemIntelligencePanel({ enabled = false }: { enabled?: boolean }) {
  const query = useGetSystemIntelligence({
    query: { queryKey: getGetSystemIntelligenceQueryKey(), refetchInterval: 30000 },
  });
  const data = query.data;
  useEffect(() => {
    if (query.isError) {
      notifySiFailure(getSiFailureCategory(query.error), enabled);
    }
  }, [enabled, query.error, query.isError]);
  return <section className="system-intelligence" aria-label="System intelligence" data-testid="system-intelligence">
    <header className="intel-heading">
      <div className="intel-title-group"><span className="intel-mark" aria-hidden="true">SI</span><div><span className="intel-kicker">XAUUSD · RESEARCH SURFACE</span><h2>SYSTEM INTELLIGENCE</h2></div></div>
      <span className="intel-live-note"><i aria-hidden="true"/> READ-ONLY · REFRESHES EVERY 30 SEC</span>
    </header>
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
        <LoadedIntelligence data={data}/>
      </> : null}
  </section>;
}

export default SystemIntelligencePanel;
