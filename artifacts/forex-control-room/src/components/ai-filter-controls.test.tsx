import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useState } from 'react';
import {
  AiFilterHiddenSummary,
  AiFilterToggle,
} from './ai-filter-controls';
import {
  applyAiPolicyView,
  type PolicyAlertItem,
  type PolicyDecisionView,
} from '@/lib/ai-policy-view';

afterEach(cleanup);

type TestItem = PolicyAlertItem & {
  alert: {
    alertId: string;
    direction: string;
    time: string;
  };
};

const items: TestItem[] = [
  {
    alert: {
      alertId: 'kept-alert',
      direction: 'BUY',
      time: '2025-01-01T10:00:00Z',
    },
  },
  {
    alert: {
      alertId: 'hidden-alert',
      direction: 'SELL',
      time: '2025-01-01T10:15:00Z',
    },
  },
];

const decisions = new Map<string, PolicyDecisionView>([
  ['kept-alert', { alertId: 'kept-alert', passed: true, reasons: [] }],
  [
    'hidden-alert',
    {
      alertId: 'hidden-alert',
      passed: false,
      reasons: ['min_strength=STRONG: alert strength is WEAK', 'require_engulf=true: alert is not engulfing'],
    },
  ],
]);

function PolicyTape({ policyMode = 'active' }: { policyMode?: string }) {
  const [enabled, setEnabled] = useState(false);
  const view = applyAiPolicyView(items, enabled, policyMode, decisions);

  return (
    <>
      <AiFilterToggle
        enabled={enabled}
        onToggle={() => setEnabled(current => !current)}
      />
      <AiFilterHiddenSummary
        hidden={view.hidden}
        formatTime={value => value}
      />
      <div data-testid="tape">
        {view.visible.map(item => (
          <span key={item.alert.alertId}>{item.alert.alertId}</span>
        ))}
      </div>
      <span data-testid="tape-count">{view.visible.length}</span>
    </>
  );
}

describe('AI alert filter controls', () => {
  it('starts off, filters the tape when enabled, and expands per-setting reasons', () => {
    render(<PolicyTape />);

    const toggle = screen.getByTestId('filter-alert-ai');
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(toggle.textContent).toBe('AI FILTER OFF');
    expect(screen.getByTestId('tape').textContent).toContain('hidden-alert');

    fireEvent.click(toggle);

    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('tape').textContent).toContain('kept-alert');
    expect(screen.getByTestId('tape').textContent).not.toContain('hidden-alert');

    const details = screen.getByTestId('ai-filter-hidden');
    expect(details.hasAttribute('open')).toBe(false);
    expect(screen.getByTestId('ai-filter-hidden-summary').textContent).toBe(
      '1 hidden by AI filter',
    );
    fireEvent.click(screen.getByTestId('ai-filter-hidden-summary'));

    expect(details.hasAttribute('open')).toBe(true);
    expect(details.textContent).toContain(
      'min_strength=STRONG: alert strength is WEAK',
    );
    expect(details.textContent).toContain(
      'require_engulf=true: alert is not engulfing',
    );
  });

  it('keeps tape counts identical when toggled under a passthrough policy', () => {
    render(<PolicyTape policyMode="passthrough" />);
    const toggle = screen.getByTestId('filter-alert-ai');
    const countBefore = screen.getByTestId('tape-count').textContent;

    fireEvent.click(toggle);

    expect(screen.getByTestId('tape-count').textContent).toBe(countBefore);
    expect(screen.getByTestId('tape').textContent).toContain('kept-alert');
    expect(screen.getByTestId('tape').textContent).toContain('hidden-alert');
    expect(screen.queryByTestId('ai-filter-hidden')).toBeNull();
  });
});
