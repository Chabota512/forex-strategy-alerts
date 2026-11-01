import { describe, expect, it } from 'vitest';
import type { Candle } from '@workspace/api-client-react';
import {
  anchorToChartCoordinate,
  annotationEditorReducer,
  annotationStorageKey,
  coordinateToChartPoint,
  initialAnnotationEditorState,
  loadAnnotations,
  normalizeRectangle,
  removeSelectedAnnotations,
  saveAnnotations,
  type ChartAnnotation,
  type PlotGeometry,
} from './chart-annotations';

const candles = [
  { time: '2023-01-31T23:58:00Z', open: 1, high: 3, low: 0, close: 2 },
  { time: '2023-01-31T23:59:00Z', open: 2, high: 4, low: 1, close: 3 },
  { time: '2023-02-01T00:00:00Z', open: 3, high: 5, low: 2, close: 4 },
  { time: '2023-02-01T00:01:00Z', open: 4, high: 6, low: 3, close: 5 },
] as Candle[];

const plot = (left: number, step: number): PlotGeometry => ({
  left,
  top: 20,
  width: 400,
  height: 200,
  step,
  low: 0,
  high: 10,
});

const line: ChartAnnotation = {
  id: 'line-1',
  kind: 'line',
  start: { time: candles[0].time, price: 2 },
  end: { time: candles[2].time, price: 8 },
};

describe('chart annotations', () => {
  it('recomputes anchored positions after pan and zoom', () => {
    const original = anchorToChartCoordinate(line.end, candles, plot(16, 80));
    const panned = anchorToChartCoordinate(line.end, candles.slice(1), plot(16, 80));
    const zoomed = anchorToChartCoordinate(line.end, candles, plot(16, 40));

    expect(original?.x).toBe(216);
    expect(panned?.x).toBe(136);
    expect(zoomed?.x).toBe(116);
    expect(original?.y).toBe(60);
    expect(zoomed?.y).toBe(original?.y);
  });

  it('maps a crosshair to the snapped candle time and pointer price', () => {
    const crosshair = coordinateToChartPoint(195, 100, candles, plot(16, 80));
    expect(crosshair).toMatchObject({
      index: 2,
      time: '2023-02-01T00:00:00Z',
      price: 6,
    });
  });

  it('switches tools and cancels an in-progress draw', () => {
    const drawing = annotationEditorReducer(initialAnnotationEditorState(), {
      type: 'begin-draw',
      kind: 'line',
      point: line.start,
    });
    expect(drawing.tool).toBe('line');
    expect(drawing.draft).not.toBeNull();

    const cancelled = annotationEditorReducer(drawing, { type: 'cancel-draw' });
    expect(cancelled.tool).toBe('cursor');
    expect(cancelled.draft).toBeNull();
  });

  it('supports draw, select, move/resize replacement, and delete', () => {
    let state = annotationEditorReducer(initialAnnotationEditorState(), {
      type: 'begin-draw',
      kind: 'line',
      point: line.start,
    });
    state = annotationEditorReducer(state, { type: 'update-draft', point: line.end });
    state = annotationEditorReducer(state, { type: 'finish-draw', id: line.id });
    expect(state.annotations).toEqual([line]);
    expect(state.selectedId).toBe(line.id);

    state = annotationEditorReducer(state, { type: 'select', id: line.id });
    const moved = {
      ...line,
      start: { ...line.start, price: 1.5 },
      end: { ...line.end, price: 7.5 },
    };
    state = annotationEditorReducer(state, { type: 'replace', annotation: moved });
    expect(state.annotations[0]).toEqual(moved);

    state = annotationEditorReducer(state, { type: 'delete-selected' });
    expect(state.annotations).toEqual([]);
  });

  it('removes only the selected drawing and reports the removed count', () => {
    const rectangle: ChartAnnotation = {
      id: 'rectangle-1',
      kind: 'rectangle',
      start: { time: candles[1].time, price: 4 },
      end: { time: candles[3].time, price: 1 },
    };
    const drawings = [line, rectangle];

    expect(removeSelectedAnnotations(drawings, rectangle.id)).toEqual({
      annotations: [line],
      clearedCount: 1,
    });
    expect(removeSelectedAnnotations(drawings, null)).toEqual({
      annotations: drawings,
      clearedCount: 0,
    });
  });

  it('normalizes rectangle corners across reverse-direction drags', () => {
    expect(normalizeRectangle(
      { time: candles[3].time, price: 2 },
      { time: candles[0].time, price: 8 },
    )).toEqual({
      start: { time: candles[0].time, price: 8 },
      end: { time: candles[3].time, price: 2 },
    });
  });

  it('keeps a resized rectangle anchored to candles and prices', () => {
    const rectangle: ChartAnnotation = {
      id: 'rect-1',
      kind: 'rectangle',
      start: { time: candles[0].time, price: 8 },
      end: { time: candles[2].time, price: 3 },
    };
    let state = annotationEditorReducer(initialAnnotationEditorState(), {
      type: 'begin-draw',
      kind: 'rectangle',
      point: rectangle.start,
    });
    state = annotationEditorReducer(state, { type: 'update-draft', point: rectangle.end });
    state = annotationEditorReducer(state, { type: 'finish-draw', id: rectangle.id });
    const resized = { ...rectangle, end: { time: candles[3].time, price: 2 } };
    state = annotationEditorReducer(state, { type: 'replace', annotation: resized });
    expect(anchorToChartCoordinate(resized.end, candles, plot(16, 80))).toMatchObject({
      x: 296,
      time: candles[3].time,
      price: 2,
    });
  });

  it('persists drawings round-trip under symbol and timeframe keys', () => {
    const data = new Map<string, string>();
    const storage = {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => data.set(key, value),
    };
    const key = annotationStorageKey('XAUUSD', 'M1');
    saveAnnotations(key, [line], storage);
    expect(loadAnnotations(key, storage)).toEqual([line]);
    expect(annotationStorageKey('XAUUSD', 'M1')).not.toBe(
      annotationStorageKey('XAUUSD', 'M5'),
    );
  });
});
