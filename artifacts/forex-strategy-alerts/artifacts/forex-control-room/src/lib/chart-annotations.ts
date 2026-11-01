import type { Candle, InstrumentSymbol, Timeframe } from '@workspace/api-client-react';

export type AnnotationTool = 'cursor' | 'line' | 'rectangle';
export type AnnotationKind = Exclude<AnnotationTool, 'cursor'>;
export type AnnotationDragMode = 'move' | 'start' | 'end' | 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w';

export type TimePricePoint = {
  time: string;
  price: number;
};

export type ChartAnnotation = {
  id: string;
  kind: AnnotationKind;
  start: TimePricePoint;
  end: TimePricePoint;
};

export type AnnotationDraft = {
  kind: AnnotationKind;
  start: TimePricePoint;
  end: TimePricePoint;
};

export type AnnotationEditorState = {
  tool: AnnotationTool;
  annotations: ChartAnnotation[];
  selectedId: string | null;
  draft: AnnotationDraft | null;
};

export type AnnotationEditorAction =
  | { type: 'set-tool'; tool: AnnotationTool }
  | { type: 'begin-draw'; kind: AnnotationKind; point: TimePricePoint }
  | { type: 'update-draft'; point: TimePricePoint }
  | { type: 'finish-draw'; id: string }
  | { type: 'cancel-draw' }
  | { type: 'select'; id: string | null }
  | { type: 'replace'; annotation: ChartAnnotation }
  | { type: 'delete-selected' }
  | { type: 'delete'; id: string }
  | { type: 'hydrate'; annotations: ChartAnnotation[] };

export type PlotGeometry = {
  left: number;
  top: number;
  width: number;
  height: number;
  step: number;
  low: number;
  high: number;
};

export type ChartCoordinate = {
  x: number;
  y: number;
  index: number;
  time: string;
  price: number;
};

export const annotationStorageKey = (
  symbol: InstrumentSymbol | string,
  timeframe: Timeframe | string,
) => `forex-chart-annotations:${symbol}:${timeframe}`;

export function initialAnnotationEditorState(
  annotations: ChartAnnotation[] = [],
): AnnotationEditorState {
  return {
    tool: 'cursor',
    annotations,
    selectedId: null,
    draft: null,
  };
}

export function removeSelectedAnnotations(
  annotations: ChartAnnotation[],
  selectedId: string | null,
): { annotations: ChartAnnotation[]; clearedCount: number } {
  if (!selectedId) return { annotations, clearedCount: 0 };
  const remaining = annotations.filter(annotation => annotation.id !== selectedId);
  return {
    annotations: remaining,
    clearedCount: annotations.length - remaining.length,
  };
}

export function normalizeRectangle(
  first: TimePricePoint,
  second: TimePricePoint,
): { start: TimePricePoint; end: TimePricePoint } {
  return {
    start: {
      time: first.time <= second.time ? first.time : second.time,
      price: Math.max(first.price, second.price),
    },
    end: {
      time: first.time <= second.time ? second.time : first.time,
      price: Math.min(first.price, second.price),
    },
  };
}

export function annotationEditorReducer(
  state: AnnotationEditorState,
  action: AnnotationEditorAction,
): AnnotationEditorState {
  switch (action.type) {
    case 'set-tool':
      return { ...state, tool: action.tool, draft: null };
    case 'begin-draw':
      return {
        ...state,
        tool: action.kind,
        selectedId: null,
        draft: { kind: action.kind, start: action.point, end: action.point },
      };
    case 'update-draft':
      if (!state.draft) return state;
      return {
        ...state,
        draft: { ...state.draft, end: action.point },
      };
    case 'finish-draw': {
      if (!state.draft) return state;
      const draft = state.draft.kind === 'rectangle'
        ? { ...state.draft, ...normalizeRectangle(state.draft.start, state.draft.end) }
        : state.draft;
      if (
        draft.start.time === draft.end.time
        && draft.start.price === draft.end.price
      ) {
        return { ...state, draft: null };
      }
      const annotation: ChartAnnotation = {
        id: action.id,
        kind: draft.kind,
        start: draft.start,
        end: draft.end,
      };
      return {
        ...state,
        annotations: [...state.annotations, annotation],
        selectedId: annotation.id,
        draft: null,
      };
    }
    case 'cancel-draw':
      return { ...state, draft: null, tool: 'cursor' };
    case 'select':
      return { ...state, selectedId: action.id };
    case 'replace':
      return {
        ...state,
        annotations: state.annotations.map(annotation =>
          annotation.id === action.annotation.id ? action.annotation : annotation,
        ),
      };
    case 'delete-selected': {
      const result = removeSelectedAnnotations(state.annotations, state.selectedId);
      return result.clearedCount
        ? { ...state, annotations: result.annotations, selectedId: null }
        : state;
    }
    case 'delete':
      return {
        ...state,
        annotations: state.annotations.filter(
          annotation => annotation.id !== action.id,
        ),
        selectedId: state.selectedId === action.id ? null : state.selectedId,
      };
    case 'hydrate':
      return {
        ...state,
        annotations: action.annotations,
        selectedId: null,
        draft: null,
      };
  }
}

export function priceToY(price: number, geometry: PlotGeometry): number {
  const range = geometry.high - geometry.low;
  if (!Number.isFinite(range) || range <= 0) return geometry.top + geometry.height / 2;
  return geometry.top + ((geometry.high - price) / range) * geometry.height;
}

export function yToPrice(y: number, geometry: PlotGeometry): number {
  if (!geometry.height) return geometry.low;
  const clamped = Math.max(geometry.top, Math.min(geometry.top + geometry.height, y));
  return geometry.high
    - ((clamped - geometry.top) / geometry.height) * (geometry.high - geometry.low);
}

export function anchorToChartCoordinate(
  point: TimePricePoint,
  candles: Array<Candle | undefined>,
  geometry: PlotGeometry,
): ChartCoordinate | null {
  const index = candles.findIndex(candle => candle?.time === point.time);
  if (index < 0 || !candles[index]) return null;
  return {
    x: geometry.left + geometry.step * (index + 0.5),
    y: priceToY(point.price, geometry),
    index,
    time: point.time,
    price: point.price,
  };
}

function nearestCandleIndex(
  candles: Array<Candle | undefined>,
  intendedIndex: number,
): number {
  if (!candles.length) return -1;
  const clamped = Math.max(0, Math.min(candles.length - 1, intendedIndex));
  if (candles[clamped]) return clamped;
  for (let offset = 1; offset < candles.length; offset += 1) {
    const before = clamped - offset;
    const after = clamped + offset;
    if (before >= 0 && candles[before]) return before;
    if (after < candles.length && candles[after]) return after;
  }
  return -1;
}

export function coordinateToChartPoint(
  x: number,
  y: number,
  candles: Array<Candle | undefined>,
  geometry: PlotGeometry,
): ChartCoordinate | null {
  if (!candles.length || !geometry.step) return null;
  const intendedIndex = Math.floor((x - geometry.left) / geometry.step);
  const index = nearestCandleIndex(candles, intendedIndex);
  const candle = candles[index];
  if (!candle) return null;
  return {
    x: geometry.left + geometry.step * (index + 0.5),
    y: priceToY(yToPrice(y, geometry), geometry),
    index,
    time: candle.time,
    price: yToPrice(y, geometry),
  };
}

export function loadAnnotations(
  key: string,
  storage?: Pick<Storage, 'getItem'>,
): ChartAnnotation[] {
  try {
    const store = storage ?? (typeof window === 'undefined' ? undefined : window.localStorage);
    const raw = store?.getItem(key);
    if (!raw) return [];
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value)) return [];
    return value.filter(isChartAnnotation);
  } catch {
    return [];
  }
}

export function saveAnnotations(
  key: string,
  annotations: ChartAnnotation[],
  storage?: Pick<Storage, 'setItem'>,
): void {
  try {
    const store = storage ?? (typeof window === 'undefined' ? undefined : window.localStorage);
    store?.setItem(key, JSON.stringify(annotations));
  } catch {
    // Chart annotation storage is optional; keep drawings available for this session.
  }
}

function isChartAnnotation(value: unknown): value is ChartAnnotation {
  if (!value || typeof value !== 'object') return false;
  const annotation = value as Partial<ChartAnnotation>;
  const validPoint = (point: unknown): point is TimePricePoint => {
    if (!point || typeof point !== 'object') return false;
    const candidate = point as Partial<TimePricePoint>;
    return typeof candidate.time === 'string'
      && Number.isFinite(candidate.price);
  };
  return typeof annotation.id === 'string'
    && (annotation.kind === 'line' || annotation.kind === 'rectangle')
    && validPoint(annotation.start)
    && validPoint(annotation.end);
}
