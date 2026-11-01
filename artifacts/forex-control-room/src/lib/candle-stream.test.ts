import { describe, expect, it, vi } from 'vitest';
import type { Candle } from '@workspace/api-client-react';
import { streamCandleWindow } from './candle-stream';

const makeCandles = (count: number) => Array.from(
  { length: count },
  () => ({ open: 1, high: 2, low: 0, close: 1 }) as unknown as Candle,
);

describe('streamCandleWindow', () => {
  it('requests month data in bounded chunks and reports progress', async () => {
    const controller = new AbortController();
    const fetched: Array<{ month: string; offset: number; limit: number }> = [];
    const progress: number[] = [];
    const onChunk = vi.fn();
    const result = await streamCandleWindow({
      chunks: [{ month: '2026-01', count: 40_000 }],
      totalCandles: 40_000,
      initialLoaded: 0,
      chunkSize: 25_000,
      softCap: 500_000,
      signal: controller.signal,
      isCached: () => false,
      fetchChunk: async (month, offset, limit) => {
        fetched.push({ month, offset, limit });
        return { chunkOffset: offset, candles: makeCandles(limit) };
      },
      onChunk,
      onProgress: count => progress.push(count),
    });

    expect(fetched).toEqual([
      { month: '2026-01', offset: 0, limit: 25_000 },
      { month: '2026-01', offset: 25_000, limit: 15_000 },
    ]);
    expect(progress).toEqual([25_000, 40_000]);
    expect(onChunk).toHaveBeenCalledTimes(2);
    expect(result).toEqual({ loaded: 40_000, capped: false, cancelled: false });
  });

  it('stops at the 500,000 candle soft cap', async () => {
    const controller = new AbortController();
    const fetchChunk = vi.fn(async (_month: string, offset: number, limit: number) => ({
      chunkOffset: offset,
      candles: makeCandles(limit),
    }));
    const result = await streamCandleWindow({
      chunks: [{ month: '2026-01', count: 600_000 }],
      totalCandles: 600_000,
      initialLoaded: 0,
      chunkSize: 25_000,
      softCap: 500_000,
      signal: controller.signal,
      isCached: () => false,
      fetchChunk,
      onChunk: () => undefined,
      onProgress: () => undefined,
    });

    expect(fetchChunk).toHaveBeenCalledTimes(20);
    expect(result).toEqual({ loaded: 500_000, capped: true, cancelled: false });
  });

  it('skips cached segments and cancels before requesting the next segment', async () => {
    const controller = new AbortController();
    const fetchChunk = vi.fn(async (_month: string, offset: number, limit: number) => ({
      chunkOffset: offset,
      candles: makeCandles(limit),
    }));
    const result = await streamCandleWindow({
      chunks: [{ month: '2026-01', count: 75_000 }],
      totalCandles: 75_000,
      initialLoaded: 25_000,
      chunkSize: 25_000,
      softCap: 500_000,
      signal: controller.signal,
      isCached: (_month, offset) => offset === 0,
      fetchChunk,
      onChunk: () => undefined,
      onProgress: () => controller.abort(),
    });

    expect(fetchChunk).toHaveBeenCalledTimes(1);
    expect(fetchChunk).toHaveBeenCalledWith(
      '2026-01',
      25_000,
      25_000,
      controller.signal,
    );
    expect(result.cancelled).toBe(true);
    expect(result.loaded).toBe(50_000);
  });
});
