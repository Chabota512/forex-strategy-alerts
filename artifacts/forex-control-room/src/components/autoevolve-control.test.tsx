import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import AutoevolveControl from './autoevolve-control';

const api = vi.hoisted(() => ({
  status: vi.fn(),
  history: vi.fn(),
  start: vi.fn(),
  stop: vi.fn(),
  restore: vi.fn(),
  invalidate: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: api.invalidate }),
}));

vi.mock('@workspace/api-client-react', () => ({
  getGetAutoevolveStatusQueryKey: () => ['autoevolve-status'],
  getGetEvolvePolicyHistoryQueryKey: () => ['evolve-policy-history'],
  getGetEvolvePolicyQueryKey: () => ['evolve-policy'],
  useGetAutoevolveStatus: () => api.status(),
  useGetEvolvePolicyHistory: () => api.history(),
  useStartAutoevolve: () => ({ mutate: api.start, isPending: false, isError: false, error: null }),
  useStopAutoevolve: () => ({ mutate: api.stop, isPending: false, isError: false, error: null }),
  useRestoreEvolvePolicy: () => ({ mutate: api.restore, isPending: false, isError: false, error: null }),
}));

beforeEach(() => {
  window.localStorage.clear();
  api.status.mockReturnValue({
    data: {
      state: 'stopped',
      running: false,
      phase: 'stopped',
      round: 0,
      last_verdict: null,
      last_result_file: null,
      active_job_id: null,
      swing_version: 'V2',
      cooldown_seconds: 60,
      cooldown_until_utc: null,
      stop_requested: false,
      keys: { key1: 'unknown', key2: 'unknown' },
      alerts: [],
      error: null,
      updated_at_utc: '2026-11-02T00:00:00Z',
    },
    isLoading: false,
    isError: false,
    error: null,
  });
  api.history.mockReturnValue({
    data: { versions: [] },
    isLoading: false,
    isError: false,
    error: null,
  });
  api.start.mockReset();
  api.stop.mockReset();
  api.restore.mockReset();
  api.invalidate.mockReset();
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe('AUTOEVOLVE dataset gate', () => {
  it('blocks start and explains the requirement when XAUUSD data is unavailable', () => {
    render(<AutoevolveControl datasetStatus="unavailable"/>);

    expect((screen.getByTestId('button-start-autoevolve') as HTMLButtonElement).disabled)
      .toBe(true);
    expect(screen.getByTestId('autoevolve-dataset-unavailable').textContent)
      .toContain('owner-approved XAUUSD dataset');
    fireEvent.click(screen.getByTestId('button-start-autoevolve'));
    expect(api.start).not.toHaveBeenCalled();
  });

  it('enables start after an approved XAUUSD dataset is available', () => {
    render(<AutoevolveControl datasetStatus="approved"/>);

    expect((screen.getByTestId('button-start-autoevolve') as HTMLButtonElement).disabled)
      .toBe(false);
    expect(screen.queryByTestId('autoevolve-dataset-unavailable')).toBeNull();
  });

  it('shows the retry countdown while AUTOEVOLVE waits to retry', () => {
    const currentStatus = api.status();
    api.status.mockReturnValue({
      ...currentStatus,
      data: {
        ...currentStatus.data,
        state: 'cooldown',
        running: true,
        phase: 'retry-wait',
        cooldown_seconds: 20,
        cooldown_until_utc: new Date(Date.now() + 20_000).toISOString(),
      },
    });

    render(<AutoevolveControl datasetStatus="approved"/>);

    expect(screen.getByTestId('autoevolve-retry-countdown').textContent)
      .toMatch(/RETRY IN \d+s/);
  });
});
