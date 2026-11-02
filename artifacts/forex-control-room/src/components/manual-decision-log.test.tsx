import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ManualDecisionLog, type ManualDecisionRecord } from './manual-decision-log';

afterEach(cleanup);

const record: ManualDecisionRecord = {
  caseId: 'case-001',
  alertId: 'alert-123456789',
  decision: 'take',
  formationFingerprint: 'fingerprint-123456789',
  featureContext: {
    cct: 'uptrend',
    cst: 'transition',
    wick_pressure: 'up-pressure',
  },
  comment: 'Following the trend',
  tradeDirection: 'BUY',
};

describe('ManualDecisionLog', () => {
  it('starts collapsed and shows manual decisions in a table when expanded', () => {
    render(<ManualDecisionLog records={[record]} />);

    const log = screen.getByTestId('manual-decision-log') as HTMLDetailsElement;
    expect(log.open).toBe(false);
    expect(screen.getByTestId('manual-decision-log-count').textContent).toBe('01 ENTRY');

    fireEvent.click(screen.getByTestId('manual-decision-log-toggle'));

    expect(log.open).toBe(true);
    expect(screen.getByRole('table', { name: 'Manual mode trade log' })).toBeTruthy();
    expect(screen.getByText('TAKE')).toBeTruthy();
    expect(screen.getByText('BUY')).toBeTruthy();
    expect(screen.getByText(/CCT uptrend · CST transition · WP up-pressure/)).toBeTruthy();
    expect(screen.getByText('Following the trend')).toBeTruthy();
  });

  it('shows an empty message when there are no manual decisions', () => {
    render(<ManualDecisionLog records={[]} />);

    fireEvent.click(screen.getByTestId('manual-decision-log-toggle'));

    expect(screen.getByText('No manual decisions recorded in this replay.')).toBeTruthy();
  });
});
