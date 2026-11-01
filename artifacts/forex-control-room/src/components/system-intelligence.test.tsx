import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SystemIntelligence as IntelligenceData } from '@workspace/api-client-react';
import SystemIntelligence from './system-intelligence';

const { useGetSystemIntelligence, getGetSystemIntelligenceQueryKey } = vi.hoisted(() => ({
  useGetSystemIntelligence: vi.fn(),
  getGetSystemIntelligenceQueryKey: vi.fn(() => ['system-intelligence']),
}));

vi.mock('@workspace/api-client-react', () => ({
  useGetSystemIntelligence,
  getGetSystemIntelligenceQueryKey,
}));

class ResizeObserverStub implements ResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    this.callback([
      {
        target,
        contentRect: { width: 640, height: 190 } as DOMRectReadOnly,
      } as ResizeObserverEntry,
    ], this);
  }

  unobserve() {}
  disconnect() {}
}

const sampleData: IntelligenceData = {
  symbol: 'XAUUSD',
  informationalOnly: true,
  caseCount: 12,
  overall: {
    hits: 7, misses: 5, hitRate: 58.3, expectancy: 0.27, expectancyMoney: -0.21,
    capitalContext: { balance_usd: 1000, sizing: 'LOTS', lots: { XAUUSD: 0.01 } },
  },
  rules: [{
    id: 'rule-a',
    name: 'Trend confirmation',
    description: 'Require trend context to agree with the alert.',
    samples: 12,
    hits: 7,
    misses: 5,
    hitRate: 58.3,
    expectancy: 0.27,
    expectancyMoney: -0.21,
    capitalContext: { balance_usd: 1000, sizing: 'LOTS', lots: { XAUUSD: 0.01 } },
    baselineHitRate: 51.2,
    weight: 0.65,
    flagged: true,
  }],
  hitRateOverTime: [{
    at: '2025-01-01T00:00:00Z', caseCount: 12, hitRate: 58.3,
    expectancy: 0.27, expectancyMoney: -0.21,
    capitalContext: { balance_usd: 1000, sizing: 'LOTS', lots: { XAUUSD: 0.01 } },
  }],
  lessons: [{
    id: 'lesson-1',
    ruleId: 'rule-a',
    ruleName: 'Trend confirmation',
    action: 'Retain the rule for further observation.',
    evidence: { sample_count: 12, confidence: 'early' },
    createdAtUtc: '2025-01-02T12:00:00Z',
  }],
  tunables: {
    strengthWeights: { trend: 0.65 },
    strengthThresholds: { strong: 0.72 },
    invertWeights: { direction: -0.25 },
  },
};

describe('SystemIntelligence', () => {
  beforeEach(() => {
    useGetSystemIntelligence.mockReset();
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders the generated research summary and evidence surfaces', () => {
    useGetSystemIntelligence.mockReturnValue({
      data: sampleData, isLoading: false, isError: false, refetch: vi.fn(),
    });

    render(<SystemIntelligence/>);

    expect(screen.getAllByText('58.3%').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Trend confirmation').length).toBeGreaterThan(0);
    expect(screen.getByRole('img', { name: 'XAUUSD hit rate over time' })).toBeTruthy();
    expect(screen.getByTestId('intel-rules').textContent).toContain('HIT RATE');
    expect(screen.getByText('Retain the rule for further observation.')).toBeTruthy();
    expect(screen.getAllByText(/−\$0\.21\/trade/).length).toBeGreaterThan(0);
    expect(screen.getByText(/CAPITAL · \$1,000 · LOTS · 0\.01 XAUUSD/)).toBeTruthy();
    expect(screen.getByText(/SAMPLE COUNT:/)).toBeTruthy();
    expect(screen.getByText('STRENGTH THRESHOLDS')).toBeTruthy();
    expect((screen.getByTestId('button-accept-intel-suggestion') as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/SIMULATION ONLY/)).toBeTruthy();
  });

  it('shows cluster-stat evidence on a pending proposal', () => {
    const data = {
      ...sampleData,
      proposal: {
        threshold: 50,
        newResolvedCases: 0,
        eligible: false,
        generating: false,
        pending: {
          proposal_id: 'proposal-1',
          status: 'pending',
          candidate: {
            id: 'rule-proposal-1',
            name: 'Uptrend follow-through',
            description: 'Track the cluster.',
            rationale: 'The uptrend cluster is supported by resolved cases.',
            condition: { all: [{ field: 'features.cct', op: 'eq', value: 'uptrend' }] },
          },
          evidence: {
            resolvedCaseCount: 50,
            minimumClusterSize: 30,
            interpretation: 'Single-feature aggregate statistics; not a joint rule backtest.',
            clusters: [{
              condition: { all: [{ field: 'features.cct', op: 'eq', value: 'uptrend' }] },
              sampleCount: 50,
              hitRate: 0.7,
              expectancyR: 0.4,
              fadeVsFollow: { pairedAlerts: 18, meanFadeMinusFollowR: -0.2 },
            }],
          },
        },
        latestAttempt: null,
        attemptCount: 1,
        tokenUsage: { input: null, output: null, total: null },
      },
    } as IntelligenceData;
    useGetSystemIntelligence.mockReturnValue({
      data, isLoading: false, isError: false, refetch: vi.fn(),
    });

    render(<SystemIntelligence/>);

    const evidence = screen.getByTestId('intel-proposal-evidence');
    expect(evidence.textContent).toContain('50 RESOLVED CASES');
    expect(evidence.textContent).toContain('n 50');
    expect(evidence.textContent).toContain('HIT 70.0%');
    expect(evidence.textContent).toContain('E[R] 0.40R');
    expect(evidence.textContent).toContain('FADE−FOLLOW -0.20R · 18 PAIRS');
    expect(evidence.textContent).toContain('not a joint rule backtest');
  });

  it('offers a retry action when the research request fails', () => {
    const refetch = vi.fn();
    useGetSystemIntelligence.mockReturnValue({
      data: undefined, isLoading: false, isError: true, error: new Error('Unavailable'), refetch,
    });

    render(<SystemIntelligence/>);
    fireEvent.click(screen.getByTestId('button-retry-intelligence'));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('shows a composed no-cases state', () => {
    useGetSystemIntelligence.mockReturnValue({
      data: { ...sampleData, caseCount: 0 }, isLoading: false, isError: false, refetch: vi.fn(),
    });
    render(<SystemIntelligence/>);
    expect(screen.getByTestId('intel-empty')).toBeTruthy();
    expect(screen.getByText(/There is no historical sample/)).toBeTruthy();
    expect(screen.getByText(/SIMULATION ONLY/)).toBeTruthy();
  });

  it('shows newest lessons first and keeps the ledger scrollable', () => {
    const lessons = Array.from({ length: 8 }, (_, index) => ({
      ...sampleData.lessons[0],
      id: `lesson-${index + 1}`,
      action: `lesson action ${index + 1}`,
    }));
    useGetSystemIntelligence.mockReturnValue({
      data: { ...sampleData, lessons }, isLoading: false, isError: false, refetch: vi.fn(),
    });

    render(<SystemIntelligence/>);

    const ledger = screen.getByTestId('intel-lessons');
    expect(ledger.style.maxHeight).toBe('320px');
    expect(ledger.style.overflowY).toBe('auto');
    expect(ledger.firstElementChild?.textContent).toContain('lesson action 8');
    expect(ledger.children).toHaveLength(8);
  });
});
