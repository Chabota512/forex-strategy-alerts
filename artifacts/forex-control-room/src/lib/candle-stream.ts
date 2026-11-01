import type { Candle } from '@workspace/api-client-react';

export interface StreamMonthChunk {
  month: string;
  count: number;
}

export interface CandleStreamResult {
  loaded: number;
  capped: boolean;
  cancelled: boolean;
}

export async function streamCandleWindow(input: {
  chunks: StreamMonthChunk[];
  totalCandles: number;
  initialLoaded: number;
  chunkSize: number;
  softCap: number;
  signal: AbortSignal;
  isCached: (month: string, localOffset: number) => boolean;
  fetchChunk: (
    month: string,
    localOffset: number,
    limit: number,
    signal: AbortSignal,
  ) => Promise<{ chunkOffset: number; candles: Candle[] }>;
  onChunk: (
    month: string,
    localOffset: number,
    response: { chunkOffset: number; candles: Candle[] },
  ) => void;
  onProgress: (loaded: number) => void;
}): Promise<CandleStreamResult> {
  const target = Math.min(input.totalCandles, input.softCap);
  let loaded = Math.min(target, Math.max(0, input.initialLoaded));
  for (const chunk of input.chunks) {
    for (
      let localOffset = 0;
      localOffset < chunk.count && loaded < input.softCap;
      localOffset += input.chunkSize
    ) {
      if (input.signal.aborted) {
        return {
          loaded,
          capped: input.totalCandles > input.softCap,
          cancelled: true,
        };
      }
      if (input.isCached(chunk.month, localOffset)) continue;
      const limit = Math.min(
        input.chunkSize,
        chunk.count - localOffset,
        target - loaded,
      );
      const response = await input.fetchChunk(
        chunk.month,
        localOffset,
        limit,
        input.signal,
      );
      if (input.signal.aborted) {
        return {
          loaded,
          capped: input.totalCandles > input.softCap,
          cancelled: true,
        };
      }
      input.onChunk(chunk.month, localOffset, response);
      loaded = Math.min(target, loaded + response.candles.length);
      input.onProgress(loaded);
    }
  }
  return {
    loaded,
    capped: input.totalCandles > input.softCap,
    cancelled: input.signal.aborted,
  };
}
