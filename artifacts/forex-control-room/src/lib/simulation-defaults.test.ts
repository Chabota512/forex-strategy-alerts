import { describe, expect, it } from 'vitest';
import { DEFAULT_LOT_SIZES } from './simulation-defaults';

describe('instrument lot defaults', () => {
  it('uses named defaults for gold, major forex, and JPY pairs', () => {
    expect(DEFAULT_LOT_SIZES).toEqual({
      XAUUSD: 0.01,
      GBPUSD: 0.1,
      USDJPY: 0.1,
      GBPJPY: 0.1,
      AUDJPY: 0.1,
    });
  });
});
