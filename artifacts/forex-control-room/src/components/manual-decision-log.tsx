export type ManualDecisionRecord = {
  caseId: string;
  alertId: string;
  decision: 'take' | 'skip';
  formationFingerprint: string;
  featureContext: Record<string, unknown>;
  comment: string | null;
  tradeDirection?: 'BUY' | 'SELL';
};

export function ManualDecisionLog({ records }: { records: ManualDecisionRecord[] }) {
  const orderedRecords = [...records].reverse();

  return <details
    className="manual-decision-log"
    aria-label="Manual trade log"
    data-testid="manual-decision-log"
  >
    <summary data-testid="manual-decision-log-toggle">
      <span className="manual-decision-log-title">MANUAL TRADE LOG</span>
      <span className="manual-decision-log-count" data-testid="manual-decision-log-count">
        {records.length.toString().padStart(2, '0')} {records.length === 1 ? 'ENTRY' : 'ENTRIES'}
      </span>
      <span className="manual-decision-log-hint" aria-hidden="true">EXPAND / COLLAPSE</span>
    </summary>
    <div className="manual-decision-table-wrap">
      <table className="manual-decision-table" aria-label="Manual mode trade log">
        <thead>
          <tr>
            <th scope="col">ACTION</th>
            <th scope="col">SIDE</th>
            <th scope="col">ALERT</th>
            <th scope="col">SETUP</th>
            <th scope="col">CCT / CST / WP</th>
            <th scope="col">COMMENT</th>
          </tr>
        </thead>
        <tbody>
          {!orderedRecords.length && <tr>
            <td className="manual-decision-empty" colSpan={6}>No manual decisions recorded in this replay.</td>
          </tr>}
          {orderedRecords.map(decision => <tr
            key={decision.caseId}
            data-testid={`manual-decision-row-${decision.caseId}`}
          >
            <td>
              <span className={`manual-decision-action is-${decision.decision}`}>
                {decision.decision.toUpperCase()}
              </span>
            </td>
            <td>{decision.tradeDirection ?? '—'}</td>
            <td title={decision.alertId}><code>{decision.alertId.slice(0, 10)}</code></td>
            <td title={decision.formationFingerprint}><code>{decision.formationFingerprint.slice(0, 12)}</code></td>
            <td title={`CCT ${String(decision.featureContext.cct ?? '—')} · CST ${String(decision.featureContext.cst ?? '—')} · WP ${String(decision.featureContext.wick_pressure ?? '—')}`}>
              CCT {String(decision.featureContext.cct ?? '—')} · CST {String(decision.featureContext.cst ?? '—')} · WP {String(decision.featureContext.wick_pressure ?? '—')}
            </td>
            <td title={decision.comment ?? ''}>{decision.comment?.trim() || '—'}</td>
          </tr>)}
        </tbody>
      </table>
    </div>
  </details>;
}
