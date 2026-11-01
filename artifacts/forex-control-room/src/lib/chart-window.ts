/**
 * Keep the replay cursor near 70% of the chart width, leaving forward candles
 * visible as context while still clamping the viewport to the loaded dataset.
 */
export function followCursorChartStart(
  cursor: number,
  displayEnd: number,
  viewSize: number,
): number {
  const safeViewSize = Math.max(1, Math.floor(viewSize));
  const maxStart = Math.max(0, displayEnd - safeViewSize + 1);
  const cursorPosition = Math.floor((safeViewSize - 1) * 0.7);
  return Math.max(0, Math.min(Math.floor(cursor) - cursorPosition, maxStart));
}

export function isManualChartPan(startX: number, currentX: number): boolean {
  return Math.abs(startX - currentX) > 3;
}
