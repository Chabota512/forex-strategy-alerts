import { beforeEach, describe, expect, it, vi } from 'vitest';

const toastError = vi.hoisted(() => vi.fn());

vi.mock('sonner', () => ({ toast: { error: toastError } }));

import { getSiFailureCategory, notifySiFailure } from './si-failure-toast';

describe('System Intelligence failure toasts', () => {
  beforeEach(() => toastError.mockClear());

  it('maps provider taxonomy to owner-facing reasons', () => {
    const cases = [
      ['quota', 'AI quota exhausted — update the key at console'],
      ['rate_limit', 'AI busy — will retry automatically'],
      ['high-demand', 'AI busy — will retry automatically'],
      ['auth', 'AI key invalid'],
      ['network', 'Connection issue'],
    ] as const;

    cases.forEach(([category, message], index) => {
      expect(notifySiFailure(category, true, index * 300_001)).toBe(true);
      expect(toastError).toHaveBeenLastCalledWith(message);
    });
  });

  it('throttles each failure class for five minutes and stays silent when SI is off', () => {
    expect(notifySiFailure('quota', true, 1_000_000)).toBe(true);
    expect(notifySiFailure('quota', true, 1_000_000 + 299_999)).toBe(false);
    expect(notifySiFailure('quota', true, 1_000_000 + 300_000)).toBe(true);
    expect(notifySiFailure('auth', false, 2_000_000)).toBe(false);
    expect(toastError).toHaveBeenCalledTimes(2);
  });

  it('classifies API errors using the exposed provider category and status', () => {
    expect(getSiFailureCategory({ data: { failureCategory: 'quota' } })).toBe('quota');
    expect(getSiFailureCategory({ status: 429 })).toBe('rate_limit');
    expect(getSiFailureCategory({ status: 503 })).toBe('network');
    expect(getSiFailureCategory(new TypeError('offline'))).toBe('network');
  });
});
