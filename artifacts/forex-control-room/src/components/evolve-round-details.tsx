import type { EvolveArchivedRound } from '@workspace/api-client-react';

type DetailRecord = Record<string, unknown>;

function asRecord(value: unknown): DetailRecord | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as DetailRecord
    : null;
}

function stringField(record: DetailRecord | null, key: string): string | null {
  const value = record?.[key];
  return typeof value === 'string' && value.trim() ? value : null;
}

function numberField(record: DetailRecord | null, key: string): number | null {
  const value = record?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function recordField(record: DetailRecord | null, key: string): DetailRecord | null {
  return asRecord(record?.[key]);
}

function humanizeText(value: string): string {
  return value.replace(/\b[a-z0-9]+(?:_[a-z0-9]+)+\b/gi, word => word.replace(/_/g, ' '));
}

function titleCase(value: string): string {
  return humanizeText(value)
    .replace(/\b[a-z]/g, letter => letter.toUpperCase());
}

export function roundDecisionLabel(decision: string, eligible: boolean): string {
  if (eligible) return 'ELIGIBLE';
  switch (decision.toLowerCase()) {
    case 'not_eligible': return 'NOT ELIGIBLE';
    case 'malformed_proposal': return 'PROPOSAL INCOMPLETE';
    case 'request_error': return 'REQUEST FAILED';
    case 'awaiting_owner_decision': return 'AWAITING REVIEW';
    case 'promoted': return 'PROMOTED';
    default: return titleCase(decision).toUpperCase();
  }
}

function decisionTitle(decision: string, eligible: boolean): string {
  if (eligible) return 'Eligible for promotion';
  switch (decision.toLowerCase()) {
    case 'not_eligible': return 'Not eligible for promotion';
    case 'malformed_proposal': return 'Proposal could not be evaluated';
    case 'request_error': return 'The AI request failed';
    case 'awaiting_owner_decision': return 'Waiting for your review';
    case 'promoted': return 'Strategy promoted';
    default: return titleCase(decision);
  }
}

function formatCount(value: number | null): string {
  return value === null ? 'Not recorded' : value.toLocaleString('en-US');
}

function formatR(value: number | null): string {
  if (value === null) return 'Not recorded';
  return `${value > 0 ? '+' : ''}${value.toFixed(4)} R`;
}

function formatCurrency(value: number | null): string {
  if (value === null) return 'Not recorded';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value);
}

function monthYear(value: string): string {
  const match = value.match(/^(\d{4})-(\d{2})$/);
  if (!match) return value;
  const year = Number(match[1]);
  const month = Number(match[2]);
  if (month < 1 || month > 12) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, month - 1, 1)));
}

function formatPeriod(value: string | null): string | null {
  if (!value) return null;
  const match = value.match(/^(\d{4}-\d{2})\s+through\s+(\d{4}-\d{2})$/);
  return match ? `${monthYear(match[1])} – ${monthYear(match[2])}` : value;
}

function simpleValue(value: unknown): string {
  if (value === null || value === undefined) return 'not set';
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString('en-US') : 'not recorded';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.map(simpleValue).join(', ') || 'none';
  return 'updated';
}

function proposalChanges(proposal: DetailRecord | null, fallback: string): string[] {
  const diff = proposal?.diff;
  if (Array.isArray(diff)) {
    const changes = diff.flatMap(item => {
      const change = asRecord(item);
      if (!change) return [];
      const path = stringField(change, 'path') ?? 'strategy setting';
      const from = change.old_value;
      const to = change.value;
      if (path === 'timeframes' && Array.isArray(from) && Array.isArray(to)) {
        const oldFrames = from.filter((frame): frame is string => typeof frame === 'string');
        const newFrames = to.filter((frame): frame is string => typeof frame === 'string');
        const removed = oldFrames.filter(frame => !newFrames.includes(frame));
        if (removed.length && newFrames.length) {
          return [`Use ${newFrames.join(' and ')} only; remove ${removed.join(' and ')}.`];
        }
      }
      return [`${titleCase(path)}: ${simpleValue(from)} → ${simpleValue(to)}`];
    });
    if (changes.length) return changes;
  }
  if (proposal?.no_change === true) return ['No strategy settings changed.'];
  if (fallback && fallback !== 'No structured diff recorded.') {
    return [humanizeText(fallback)];
  }
  return ['No structured strategy change was recorded.'];
}

function resultReason(
  detail: DetailRecord,
  decision: string,
  eligible: boolean,
  candidateValidation: DetailRecord | null,
): string {
  const reason = stringField(detail, 'reason');
  if (reason) return humanizeText(reason);

  const requestError = stringField(recordField(detail, 'request_error_detail'), 'message');
  if (requestError) return `The provider reported: ${humanizeText(requestError)}`;

  const expectancy = numberField(candidateValidation, 'expectancy_after_costs');
  if (!eligible && expectancy !== null && expectancy <= 0) {
    return `The proposal's after-cost expectancy was ${formatR(expectancy)}. It must be above 0 R to qualify.`;
  }
  if (decision === 'malformed_proposal') {
    return 'The proposal could not be read as a valid strategy change, so it was not evaluated.';
  }
  if (eligible) return 'The proposal passed the recorded promotion checks.';
  return 'No additional explanation was recorded for this result.';
}

function timeframeNames(data: DetailRecord | null): string[] {
  const byTimeframe = recordField(data, 'by_timeframe');
  if (!byTimeframe) return [];
  return Object.keys(byTimeframe).sort((left, right) => {
    const leftValue = Number(left.match(/\d+/)?.[0] ?? Number.MAX_SAFE_INTEGER);
    const rightValue = Number(right.match(/\d+/)?.[0] ?? Number.MAX_SAFE_INTEGER);
    return leftValue - rightValue;
  });
}

function activeTimeframes(comparison: DetailRecord | null, candidate: DetailRecord | null): string[] {
  const fromComparison = comparison?.active_timeframes;
  if (Array.isArray(fromComparison)) {
    const values = fromComparison.filter((item): item is string => typeof item === 'string');
    if (values.length) return values;
  }
  return timeframeNames(candidate);
}

function MetricComparison({
  title,
  current,
  proposed,
  currentLabel,
  proposedLabel,
  testId,
}: {
  title: string;
  current: DetailRecord | null;
  proposed: DetailRecord | null;
  currentLabel: string;
  proposedLabel: string;
  testId: string;
}) {
  if (!current && !proposed) return null;
  const period = formatPeriod(
    stringField(proposed, 'period') ?? stringField(current, 'period'),
  );
  const rows = [
    {
      label: 'Closed trades',
      current: formatCount(numberField(current, 'closed_trade_count')),
      proposed: formatCount(numberField(proposed, 'closed_trade_count')),
    },
    {
      label: 'Expectancy after costs',
      current: formatR(numberField(current, 'expectancy_after_costs')),
      proposed: formatR(numberField(proposed, 'expectancy_after_costs')),
    },
    {
      label: 'Net result after costs',
      current: formatCurrency(numberField(current, 'net_pnl_after_costs')),
      proposed: formatCurrency(numberField(proposed, 'net_pnl_after_costs')),
    },
  ];

  return <section className="evolve-history-section" data-testid={testId}>
    <div className="evolve-history-section-heading">
      <h4>{title}</h4>
      {period && <span>{period}</span>}
    </div>
    <div className="evolve-history-table-wrap">
      <table className="evolve-history-comparison">
        <thead><tr>
          <th scope="col">Measure</th>
          <th scope="col">{currentLabel}</th>
          <th scope="col">{proposedLabel}</th>
        </tr></thead>
        <tbody>{rows.map(row => <tr key={row.label}>
          <th scope="row">{row.label}</th>
          <td>{row.current}</td>
          <td>{row.proposed}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </section>;
}

type Check = { label: string; detail: string; passed: boolean };

function validationChecks(
  comparison: DetailRecord | null,
  candidateValidation: DetailRecord | null,
  monthCoverage: DetailRecord | null,
): Check[] {
  const checks: Check[] = [];
  const expectancy = numberField(candidateValidation, 'expectancy_after_costs');
  const positiveExpectancy = comparison?.expectancy_positive;
  if (typeof positiveExpectancy === 'boolean' || expectancy !== null) {
    checks.push({
      label: 'Positive expectancy after costs',
      detail: `${formatR(expectancy)}; must be above 0 R`,
      passed: typeof positiveExpectancy === 'boolean'
        ? positiveExpectancy
        : (expectancy ?? 0) > 0,
    });
  }

  const floors = recordField(comparison, 'validation_trade_floors');
  if (floors) {
    Object.entries(floors).forEach(([timeframe, value]) => {
      const floor = asRecord(value);
      if (!floor) return;
      const observed = numberField(floor, 'closed_trade_count');
      const required = numberField(floor, 'required');
      checks.push({
        label: `${timeframe} trade sample`,
        detail: `${formatCount(observed)} trades; ${formatCount(required)} required`,
        passed: floor.passed === true,
      });
    });
  }

  if (monthCoverage) {
    const covered = numberField(monthCoverage, 'covered_count');
    const required = numberField(monthCoverage, 'required_count');
    checks.push({
      label: 'Historical month coverage',
      detail: `${formatCount(covered)} months recorded; ${formatCount(required)} required`,
      passed: monthCoverage.passed === true,
    });
  }
  return checks;
}

export default function EvolveRoundDetails({ round }: { round: EvolveArchivedRound }) {
  const detail = asRecord(round.detail) ?? {};
  const proposal = recordField(detail, 'proposal');
  const comparison = recordField(detail, 'comparison');
  const championTraining = recordField(detail, 'champion_training');
  const candidateTraining = recordField(detail, 'candidate_training');
  const championValidation = recordField(detail, 'champion_validation');
  const candidateValidation = recordField(detail, 'candidate_validation');
  const coverage = recordField(comparison, 'annual_signal_month_coverage')
    ?? recordField(detail, 'candidate_month_coverage');
  const checks = validationChecks(comparison, candidateValidation, coverage);
  const decision = stringField(detail, 'decision') ?? round.decision;
  const title = decisionTitle(decision, round.eligible);
  const reason = resultReason(detail, decision, round.eligible, candidateValidation);
  const changes = proposalChanges(proposal, round.diff_summary);
  const expectedEffect = stringField(proposal, 'expected_effect');
  const rationale = stringField(proposal, 'rationale');
  const baselineVersion = stringField(detail, 'baseline_version');
  const currentFrames = timeframeNames(championTraining).join(' + ');
  const proposedFrames = activeTimeframes(comparison, candidateTraining).join(' + ');
  const currentLabel = currentFrames ? `Current · ${currentFrames}` : 'Current strategy';
  const proposedLabel = proposedFrames ? `Proposal · ${proposedFrames}` : 'Proposal';
  const tokenUsage = asRecord(round.token_usage);
  const provider = round.provider
    ? `${titleCase(round.provider)}${round.key_index ? ` · configured key ${round.key_index}` : ''}`
    : 'Not recorded';

  return <div className="evolve-history-readable" data-testid={`evolve-history-readable-${round.file}`}>
    <header className="evolve-history-detail-meta">
      <strong>Round {round.round}</strong>
      {baselineVersion && <span>Baseline strategy {baselineVersion}</span>}
    </header>

    <section className={`evolve-history-outcome${round.eligible ? ' is-eligible' : ' is-ineligible'}`}>
      <span className="evolve-history-kicker">ROUND RESULT</span>
      <h3>{title}</h3>
      <p>{reason}</p>
    </section>

    <section className="evolve-history-section">
      <h4>What changed</h4>
      <ul className="evolve-history-changes">
        {changes.map((change, index) => <li key={`${change}-${index}`}>{change}</li>)}
      </ul>
      {expectedEffect && <p className="evolve-history-expected">{humanizeText(expectedEffect)}</p>}
    </section>

    <MetricComparison
      title="Validation results"
      current={championValidation}
      proposed={candidateValidation}
      currentLabel={currentLabel}
      proposedLabel={proposedLabel}
      testId="evolve-history-validation"
    />
    <MetricComparison
      title="Training results"
      current={championTraining}
      proposed={candidateTraining}
      currentLabel={currentLabel}
      proposedLabel={proposedLabel}
      testId="evolve-history-training"
    />

    {checks.length > 0 && <section className="evolve-history-section">
      <h4>Promotion checks</h4>
      <ul className="evolve-history-checks">
        {checks.map(check => <li className={check.passed ? 'is-pass' : 'is-fail'} key={check.label}>
          <span className="evolve-history-check-status">{check.passed ? 'Passed' : 'Not met'}</span>
          <span><strong>{check.label}</strong><small>{check.detail}</small></span>
        </li>)}
      </ul>
    </section>}

    {rationale && <details className="evolve-history-explanation">
      <summary>Read the AI’s reasoning</summary>
      <p>{humanizeText(rationale)}</p>
    </details>}

    <section className="evolve-history-usage" aria-label="AI request details">
      <span><strong>AI provider</strong>{provider}</span>
      {tokenUsage && <span><strong>Token use</strong>
        Input {formatCount(numberField(tokenUsage, 'input'))} · Output {formatCount(numberField(tokenUsage, 'output'))} · Total reported {formatCount(numberField(tokenUsage, 'total'))}
      </span>}
    </section>

    <details className="evolve-history-technical" data-testid={`evolve-history-raw-${round.file}`}>
      <summary>Technical details (raw result)</summary>
      <pre>{JSON.stringify(round.detail, null, 2)}</pre>
    </details>
  </div>;
}
