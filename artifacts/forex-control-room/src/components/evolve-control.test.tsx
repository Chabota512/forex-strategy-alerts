import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { EvolveArchivedRound, EvolveJobStatus, InstrumentSymbol } from '@workspace/api-client-react';
import EvolveControl from './evolve-control';

const api = vi.hoisted(() => ({
  status: vi.fn(),
  start: vi.fn(),
  decide: vi.fn(),
  history: vi.fn(),
}));

vi.mock('@workspace/api-client-react', () => ({
  getGetEvolveStatusQueryKey: (jobId: string) => ['evolve-status', jobId],
  useRunEvolveRound: () => ({
    mutate: api.start,
    isPending: false,
    isError: false,
  }),
  useDecideEvolveJob: () => ({
    mutate: api.decide,
    isPending: false,
    isError: false,
  }),
  useGetEvolveStatus: (...args: unknown[]) => api.status(...args),
  useGetEvolveHistory: () => api.history(),
}));

function failedStatus(): EvolveJobStatus {
  return {
    job_id: 'failed-job-1',
    symbol: 'XAUUSD',
    phase: 'failed',
    logs: [],
    result: {
      round: 1,
      decision: 'request_error',
      eligible: false,
      reason: 'LLM request failed.',
    },
    error: 'LLM request failed (quota): quota exceeded.',
    created_at: '2026-10-29T00:00:00Z',
    updated_at: '2026-10-29T00:00:01Z',
  } as unknown as EvolveJobStatus;
}

function completeStatus(): EvolveJobStatus {
  return {
    job_id: 'complete-job-1',
    symbol: 'XAUUSD',
    phase: 'complete',
    logs: [],
    result: {
      round: 2,
      decision: 'not_eligible',
      eligible: false,
      reason: 'Validation expectancy was below the required threshold.',
      proposal: { diff: [{ path: 'timeframes', value: ['M15'] }] },
      candidate_validation: { expectancy_after_costs: -0.08 },
      token_usage: { input: 40, output: 8, total: 48 },
    },
    error: null,
    created_at: '2026-10-29T00:00:00Z',
    updated_at: '2026-10-29T00:00:01Z',
  } as unknown as EvolveJobStatus;
}

function runningStatus(): EvolveJobStatus {
  return {
    job_id: 'running-job-1',
    symbol: 'XAUUSD',
    phase: 'simulating',
    logs: [],
    result: null,
    error: null,
    created_at: '2026-10-29T00:00:00Z',
    updated_at: '2026-10-29T00:00:01Z',
  } as unknown as EvolveJobStatus;
}

function archivedRound(): EvolveArchivedRound {
  return {
    file: 'evolve_round_01_20261029T020351541650Z_result.json',
    timestamp_utc: '2026-10-29T02:06:19.859626Z',
    round: 1,
    decision: 'not_eligible',
    eligible: false,
    diff_summary: 'timeframes: ["M5","M15"] → ["M15"]',
    validation_expectancy: -0.0767,
    provider: 'gemini',
    key_index: 2,
    token_usage: { input: 4866, output: 227, total: 8959 },
    detail: { decision: 'not_eligible', reason: 'Candidate validation expectancy must be positive.' },
  };
}

beforeEach(() => {
  window.localStorage.clear();
  api.status.mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  api.history.mockReturnValue({
    data: { rounds: [] },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  api.start.mockImplementation((_request, callbacks) => {
    callbacks.onSuccess(failedStatus());
  });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe('evolution round controls', () => {
  it('re-enables Run immediately after COMPLETE while keeping the verdict visible', async () => {
    api.start.mockImplementation((_request, callbacks) => {
      callbacks.onSuccess(completeStatus());
    });
    render(<EvolveControl symbol={'XAUUSD' as InstrumentSymbol} symbolApproved />);

    const runButton = screen.getByTestId('button-run-evolve-round') as HTMLButtonElement;
    fireEvent.click(runButton);

    expect(await screen.findByTestId('evolve-result')).not.toBeNull();
    expect(screen.getByText('not_eligible')).not.toBeNull();
    expect(runButton.disabled).toBe(false);
  });

  it('re-enables Run immediately after FAILED without dismissing the failure panel', async () => {
    render(<EvolveControl symbol={'XAUUSD' as InstrumentSymbol} symbolApproved />);
    const runButton = screen.getByTestId('button-run-evolve-round') as HTMLButtonElement;
    fireEvent.click(runButton);

    expect(await screen.findByTestId('evolve-failed-state')).not.toBeNull();
    expect(runButton.disabled).toBe(false);
    expect(screen.queryByTestId('evolve-review-prompt')).toBeNull();
  });

  it('keeps Run disabled while a round is active', async () => {
    api.start.mockImplementation((_request, callbacks) => {
      callbacks.onSuccess(runningStatus());
    });
    render(<EvolveControl symbol={'XAUUSD' as InstrumentSymbol} symbolApproved />);

    const runButton = screen.getByTestId('button-run-evolve-round') as HTMLButtonElement;
    fireEvent.click(runButton);

    expect(await screen.findByTestId('evolve-polling-indicator')).not.toBeNull();
    expect(runButton.disabled).toBe(true);
  });

  it('stops polling a missing saved job and allows the next round', () => {
    window.localStorage.setItem(
      'forex-lab-evolve-round',
      JSON.stringify({ jobId: 'stale-job-1' }),
    );
    api.status.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: { status: 404 },
      refetch: vi.fn(),
    });
    render(<EvolveControl symbol={'XAUUSD' as InstrumentSymbol} symbolApproved />);

    expect((screen.getByTestId('button-run-evolve-round') as HTMLButtonElement).disabled)
      .toBe(false);
    expect(screen.getByTestId('evolve-api-error').textContent)
      .toContain('Completed results remain available in Round History.');

    const statusOptions = api.status.mock.calls.at(-1)?.[1] as {
      query?: { refetchInterval?: unknown };
    };
    const refetchInterval = statusOptions.query?.refetchInterval as unknown as (
      query: unknown,
    ) => number | false;
    expect(refetchInterval({ state: { error: { status: 404 }, data: undefined } }))
      .toBe(false);
  });

  it('renders archive fixture summaries and expands the full result JSON', async () => {
    const fixture = archivedRound();
    api.history.mockReturnValue({
      data: { rounds: [fixture] },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<EvolveControl symbol={'XAUUSD' as InstrumentSymbol} symbolApproved />);

    fireEvent.click(screen.getByTestId('evolve-history-toggle'));

    const row = screen.getByTestId(`evolve-history-toggle-${fixture.file}`);
    expect(row.textContent).toContain('NOT_ELIGIBLE');
    expect(row.textContent).toContain(fixture.diff_summary);
    expect(row.textContent).toContain('EXP -0.0767 R');
    expect(row.textContent).toContain('gemini / KEY 2');
    expect(row.textContent).toContain('IN 4866');

    fireEvent.click(row);
    expect(screen.getByText(/"decision": "not_eligible"/)).not.toBeNull();
  });
});
