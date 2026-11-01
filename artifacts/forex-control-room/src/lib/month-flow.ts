export type MonthChunkRange = {
  month: string;
  startIndex: number;
  count: number;
};

export function monthForIndex(
  chunks: readonly MonthChunkRange[],
  index: number,
): string | undefined {
  return chunks.find(chunk => index >= chunk.startIndex && index < chunk.startIndex + chunk.count)?.month;
}

export function nextReplayPosition(
  cursor: number,
  totalCandles: number,
  chunks: readonly MonthChunkRange[],
): { index: number; month?: string } {
  const index = Math.max(0, Math.min(totalCandles - 1, cursor + 1));
  return { index, month: monthForIndex(chunks, index) };
}

export function monthOrdinal(month: string): number {
  const match = /^(\d{4})-(\d{2})$/.exec(month);
  if (!match) return 1;
  const year = Number(match[1]);
  const monthNumber = Number(match[2]);
  if (monthNumber < 1 || monthNumber > 12) return 1;
  return Math.max(1, (year - 2023) * 12 + monthNumber);
}
