import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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

const sampleData: IntelligenceData = {
  symbol: 'XAUUSD',
  informationalOnly: true,
  caseCount: 12,
  overall: { hits: 7, misses: 5, hitRate: 58.3, expectancy: 0.27 },
  rules: [{
    id: 'rule-a',
    name: 'Trend confirmation',
    description: 'Require trend context to agree with the alert.',
    samples: 12,
    hits: 7,
    misses: 5,
    hitRate: 58.3,
    expectancy: 0.27,
    baselineHitRate: 51.2,
    weight: 0.65,
    flagged: true,
  }],
  hitRateOverTime: [{ at: '2025-01-01T00:00:00Z', caseCount: 12, hitRate: 58.3, expectancy: 0.27 }],
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
  beforeEach(() => useGetSystemIntelligence.mockReset());

  it('renders the generated research summary and evidence surfaces', () => {
    useGetSystemIntelligence.mockReturnValue({
      data: sampleData, isLoading: false, isError: false, refetch: vi.fn(),
    });

    render(<SystemIntelligence/>);

    expect(screen.getAllByText('58.3%').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Trend confirmation').length).toBeGreaterThan(0);
    expect(screen.getByText('Retain the rule for further observation.')).toBeTruthy();
    expect(screen.getByText(/SAMPLE COUNT:/)).toBeTruthy();
    expect(screen.getByText('STRENGTH THRESHOLDS')).toBeTruthy();
    expect(screen.getByText(/SIMULATION ONLY/)).toBeTruthy();
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
});
