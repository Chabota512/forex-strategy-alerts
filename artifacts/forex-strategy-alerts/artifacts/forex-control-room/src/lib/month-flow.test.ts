import { describe, expect, it } from 'vitest';
import { monthForIndex, monthOrdinal, nextReplayPosition } from './month-flow';

describe('monthly replay flow', () => {
  const twoMonths = [
    { month: '2023-01', startIndex: 0, count: 2 },
    { month: '2023-02', startIndex: 2, count: 2 },
  ];

  it('auto-advances from the last January candle into February', () => {
    expect(nextReplayPosition(1, 4, twoMonths)).toEqual({
      index: 2,
      month: '2023-02',
    });
  });

  it('maps seek positions to their owning month', () => {
    expect(monthForIndex(twoMonths, 3)).toBe('2023-02');
    expect(monthOrdinal('2023-02')).toBe(2);
    expect(monthOrdinal('2025-12')).toBe(36);
  });
});
