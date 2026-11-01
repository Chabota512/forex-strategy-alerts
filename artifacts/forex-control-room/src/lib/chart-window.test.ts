import { describe, expect, it } from 'vitest';
import { followCursorChartStart, isManualChartPan } from './chart-window';

describe('replay chart window', () => {
  it('keeps every mocked playback cursor visible and retains candles after To', () => {
    const simulationEnd = 191;
    const displayEnd = 239;
    const viewSize = 100;

    for (let cursor = 0; cursor <= simulationEnd; cursor += 1) {
      const start = followCursorChartStart(cursor, displayEnd, viewSize);
      const end = Math.min(displayEnd, start + viewSize - 1);

      expect(start).toBeLessThanOrEqual(cursor);
      expect(end).toBeGreaterThanOrEqual(cursor);
    }

    const endCursorStart = followCursorChartStart(simulationEnd, displayEnd, viewSize);
    expect(endCursorStart + viewSize - 1).toBeGreaterThan(simulationEnd);
  });

  it('handles a short display window and a one-candle viewport', () => {
    expect(followCursorChartStart(3, 7, 100)).toBe(0);
    expect(followCursorChartStart(3, 7, 1)).toBe(3);
  });

  it('disengages follow only after a real horizontal pan, not a chart click', () => {
    expect(isManualChartPan(100, 103)).toBe(false);
    expect(isManualChartPan(100, 104)).toBe(true);
    expect(isManualChartPan(100, 96)).toBe(true);
  });
});
