import { type PointerEvent as ReactPointerEvent, type ReactNode, useCallback, useEffect, useId, useMemo, useReducer, useRef, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import NotFound from '@/pages/not-found';
import {
  getGetLabCandlesQueryKey,
  useAdvanceLabReplaySession,
  useGetAlertComment,
  useGetLabCandles,
  useGetLabDataset,
  useGetEvolvePolicy,
  useStartLabReplaySession,
} from '@workspace/api-client-react';
import type { AICommentResult, Alert, Candle, CandleMonthChunk, ConfirmedSwingEvent, EvolvePolicy, InstrumentReviewInfo, InstrumentSymbol, ReplayAlert, ReplayCandleState, ReplayPolicyDecision, ReplaySessionStartResult, SimulatedTrade, SimulationSettings, SimulationSnapshot, Timeframe, WickPressure, WickPressurePoint } from '@workspace/api-client-react';
import { ArrowDownRight, ArrowUpRight, ChevronLeft, ChevronRight, Moon, Pause, Play, RotateCcw, SkipBack, Square, Sun } from 'lucide-react';
import { Route, Switch, useLocation, Router as WouterRouter } from 'wouter';
import { SimulationPanel } from '@/components/simulation-panel';
import { DrawingLayer, DrawingToolbar, requestClearSelectedDrawings } from '@/components/drawing-tools';
import { TradeJournal } from '@/components/trade-journal';
import EvolveControl from '@/components/evolve-control';
import { AiFilterHiddenSummary, AiFilterToggle } from '@/components/ai-filter-controls';
import { SwingConnectorLayer } from '@/components/swing-connector-layer';
import { selectVisibleSwingEvents, selectVisibleSwingSegments, type SwingFamily, type SwingFamilySettings } from '@/lib/swing-events';
import { applyAiPolicyView } from '@/lib/ai-policy-view';
import { monthForIndex, monthOrdinal, nextReplayPosition } from '@/lib/month-flow';
import { DEFAULT_LOT_SIZES } from '@/lib/simulation-defaults';
import {
  annotationEditorReducer,
  annotationStorageKey,
  coordinateToChartPoint,
  initialAnnotationEditorState,
  loadAnnotations,
  normalizeRectangle,
  removeSelectedAnnotations,
  saveAnnotations,
  type AnnotationKind,
  type AnnotationTool,
  type ChartAnnotation,
  type ChartCoordinate,
  type PlotGeometry,
  type TimePricePoint,
} from '@/lib/chart-annotations';
import {
  INSTRUMENT_SYMBOLS,
  InstrumentHeading,
  persistSessionSymbol,
  SymbolSelect,
} from '@/components/instrument-controls';

const queryClient = new QueryClient();
const DISCLAIMER = 'SIMULATION — not a profitability claim.';
const XAU_MONTHS = Array.from({ length: 36 }, (_, index) => {
  const year = 2023 + Math.floor(index / 12);
  const month = String(index % 12 + 1).padStart(2, '0');
  return `${year}-${month}`;
});
const fmtTime = (value?: string) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en-GB', {
    year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'UTC',
  }).format(date) + ' UTC';
};
const fmtPrice = (value: number | undefined, symbol: InstrumentSymbol) => {
  if (typeof value !== 'number') return '—';
  const digits = symbol === 'XAUUSD' ? 2 : symbol.endsWith('JPY') ? 3 : 5;
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
};
const cctColor = (state?: string) => state === 'uptrend'
  ? 'var(--trend-up)' : state === 'downtrend' ? 'var(--trend-down)' : 'var(--trend-neutral)';
const cstColor = (state?: string) => state === 'uptrend'
  ? 'var(--trend-up)' : state === 'downtrend' ? 'var(--trend-down)' : state === 'transition' ? 'var(--trend-transition)' : 'var(--trend-neutral)';
const wickPressureColor = (state?: WickPressure) => state === 'up-pressure'
  ? 'var(--trend-up)' : state === 'down-pressure' ? 'var(--trend-down)' : 'var(--trend-neutral)';
const wickPressureLabel = (state?: WickPressure) => state
  ? state.toUpperCase().replace('-', ' ') : '—';
type AlertSortKey = 'time' | 'wp' | 'engulf' | 'variant';
const SWING_FAMILY_CHOICES: Array<{ family: SwingFamily; label: string }> = [
  { family: 'MAJOR', label: 'MAJOR' },
  { family: 'MINOR', label: 'MINOR' },
  { family: 'DC', label: 'DC' },
  { family: 'HIER', label: 'HIER' },
];
type MtfAlert = Alert & {
  mtfCst?: string | null;
  mtfCct?: string | null;
  mtfWickPressure?: string | null;
  mtfClosedAt?: string | null;
  mtfTrendAgree?: boolean;
  mtfCctAgree?: boolean;
  mtfWpAgree?: boolean;
  dualTfAlert?: boolean;
};
type AlertTapeItem = {
  candleIndex: number;
  alert: MtfAlert;
  wpState?: WickPressure;
};

type AnnotationDragMode = import('@/lib/chart-annotations').AnnotationDragMode;
type AnnotationDragState = {
  pointerId: number;
  mode: AnnotationDragMode;
  annotation: ChartAnnotation;
  pointerStartIndex: number;
  pointerStartPrice: number;
  startIndex: number;
  endIndex: number;
};
type AnnotationDrawState = {
  pointerId: number;
  kind: AnnotationKind;
  start: TimePricePoint;
  current: TimePricePoint;
  startX: number;
  startY: number;
};
type ClearDrawingsEventDetail = { clearedCount: number };

function newAnnotationId() {
  return globalThis.crypto?.randomUUID?.()
    ?? `annotation-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
}

function axisTimeLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'UTC',
  }).format(date);
}

export function Chart({ symbol, timeframe, candles, alerts, active, onAlert, showCct, showCst, showWickPressure, showDc, showHier, showAlertMarkers, showStrengthLabels, showCrosshair, showAnnotationPrices, swingEvents, showSwings, swingFamilies, trades, showTradeLevels, focusedTrade, drawingTool, onDrawingTool, replayIndex, replayReady, onReplayStep, viewStart, viewSize, onPanTo, onZoomAt, onInteraction }: {
  symbol: InstrumentSymbol;
  timeframe: Timeframe;
  candles: Array<Candle | undefined>;
  alerts: Alert[];
  active: Candle | null;
  onAlert: (alert: Alert) => void;
  showCct: boolean;
  showCst: boolean;
  showWickPressure: boolean;
  showDc: boolean;
  showHier: boolean;
  showAlertMarkers: boolean;
  showStrengthLabels: boolean;
  showCrosshair: boolean;
  showAnnotationPrices: boolean;
  swingEvents: ConfirmedSwingEvent[];
  showSwings: boolean;
  swingFamilies: SwingFamilySettings;
  trades: SimulatedTrade[];
  showTradeLevels: boolean;
  focusedTrade: SimulatedTrade | null;
  drawingTool: AnnotationTool | null;
  onDrawingTool: (tool: AnnotationTool | null) => void;
  replayIndex: number;
  replayReady: boolean;
  onReplayStep: (direction: -1 | 1) => void;
  viewStart: number;
  viewSize: number;
  onPanTo: (start: number) => void;
  onZoomAt: (anchorFraction: number, factor: number) => void;
  onInteraction: () => void;
}) {
  const width = 1100;
  const height = 480;
  const pad = { left: 16, right: 88, top: 24, bottom: 58 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const svgRef = useRef<SVGSVGElement | null>(null);
  const connectorClipPathId = `swing-plot-clip-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const pointersRef = useRef(new Map<number, { x: number; y: number }>());
  const dragRef = useRef<{ pointerId: number; startX: number; startViewStart: number; viewSize: number; moved: boolean } | null>(null);
  const annotationKey = annotationStorageKey(symbol, timeframe);
  const [annotationState, dispatchAnnotation] = useReducer(
    annotationEditorReducer,
    annotationKey,
    key => initialAnnotationEditorState(loadAnnotations(key)),
  );
  const annotationStateRef = useRef(annotationState);
  annotationStateRef.current = annotationState;
  const chartTool = drawingTool ?? 'cursor';
  useEffect(() => {
    if (annotationState.tool !== chartTool) dispatchAnnotation({ type: 'set-tool', tool: chartTool });
  }, [chartTool, annotationState.tool]);
  useEffect(() => {
    const clear = (event: Event) => {
      const detail = (event as CustomEvent<ClearDrawingsEventDetail>).detail;
      const state = annotationStateRef.current;
      const result = removeSelectedAnnotations(state.annotations, state.selectedId);
      detail.clearedCount = result.clearedCount;
      if (result.clearedCount) dispatchAnnotation({ type: 'delete-selected' });
    };
    window.addEventListener('forex-clear-selected-drawings', clear);
    return () => window.removeEventListener('forex-clear-selected-drawings', clear);
  }, []);
  const [crosshair, setCrosshair] = useState<ChartCoordinate | null>(null);
  const [chartFocused, setChartFocused] = useState(false);
  const annotationDrawRef = useRef<AnnotationDrawState | null>(null);
  const annotationDragRef = useRef<AnnotationDragState | null>(null);
  const pinchRef = useRef<{ distance: number } | null>(null);
  const ignoreClickRef = useRef(false);
  const ignoreClickTimerRef = useRef<number | null>(null);
  const span = candles;
  const validCandles = span.filter((candle): candle is Candle => Boolean(candle));
  const flatPrice = validCandles[0]?.close;
  const flatPriceWindow = validCandles.length > 0 && validCandles.every(candle =>
    candle.open === flatPrice
    && candle.high === flatPrice
    && candle.low === flatPrice
    && candle.close === flatPrice,
  );
  const levels = useMemo(() => {
    if (!validCandles.length) return null;
    const lo = Math.min(...validCandles.map(c => c.low));
    const hi = Math.max(...validCandles.map(c => c.high));
    const minSlack = symbol === 'XAUUSD' ? 0.25 : symbol.endsWith('JPY') ? 0.02 : 0.0002;
    const slack = Math.max((hi - lo) * 0.09, minSlack);
    return { low: lo - slack, high: hi + slack };
  }, [validCandles]);
  const y = (v: number) => levels ? pad.top + (levels.high - v) / (levels.high - levels.low) * plotH : 0;
  const step = plotW / Math.max(1, viewSize);
  const plotGeometry: PlotGeometry = {
    left: pad.left,
    top: pad.top,
    width: plotW,
    height: plotH,
    step,
    low: levels?.low ?? 0,
    high: levels?.high ?? 1,
  };
  useEffect(() => {
    saveAnnotations(annotationKey, annotationState.annotations);
  }, [annotationKey, annotationState.annotations]);
  useEffect(() => {
    const handleAnnotationShortcut = (event: KeyboardEvent) => {
      const target = event.target;
      if (
        target instanceof HTMLElement
        && (target.isContentEditable || target.matches('input, textarea, select'))
      ) return;
      if (event.key === 'Escape') {
        if (annotationState.tool !== 'cursor' || annotationState.draft) {
          event.preventDefault();
          annotationDrawRef.current = null;
          dispatchAnnotation({ type: 'cancel-draw' });
          onDrawingTool('cursor');
        }
      } else if (event.key === 'Delete' && annotationState.selectedId) {
        event.preventDefault();
        dispatchAnnotation({ type: 'delete-selected' });
      }
    };
    window.addEventListener('keydown', handleAnnotationShortcut);
    return () => window.removeEventListener('keydown', handleAnnotationShortcut);
  }, [annotationState.draft, annotationState.selectedId, annotationState.tool]);
  const markerMap = new Map(
    showAlertMarkers || showStrengthLabels ? alerts.map(a => [a.candle.time, a]) : [],
  );
  const activeY = active && levels ? y(active.close) : null;
  useEffect(() => {
    if (!showCrosshair || !chartFocused || !replayReady || !active || activeY === null) return;
    const activeIndex = replayIndex - viewStart;
    if (activeIndex < 0 || activeIndex >= span.length) return;
    setCrosshair({
      x: pad.left + step * (activeIndex + 0.5),
      y: activeY,
      index: activeIndex,
      time: active.time,
      price: active.close,
    });
  }, [
    active?.close,
    active?.time,
    activeY,
    chartFocused,
    replayIndex,
    replayReady,
    showCrosshair,
    span.length,
    step,
    viewStart,
  ]);
  const hasTrendTracks = showCct || showCst || showWickPressure || showDc || showHier;
  const visibleSwingEvents = selectVisibleSwingEvents(swingEvents, {
    showSwings,
    families: swingFamilies,
    replayIndex,
    viewStart,
    viewSize: span.length,
  }).filter(event => Boolean(span[event.pivot_index - viewStart]));
  const visibleSwingSegments = selectVisibleSwingSegments(swingEvents, {
    showSwings,
    families: swingFamilies,
    replayIndex,
    viewStart,
    viewSize: span.length,
  });
  const labelStride = Math.max(1, Math.floor(viewSize / 7));
  const axisLabelIndexes = new Set<number>([0, Math.max(0, span.length - 1)]);
  for (let index = labelStride; index < span.length - 1; index += labelStride) {
    if (index < span.length - 1 - Math.max(1, Math.floor(labelStride / 2))) {
      axisLabelIndexes.add(index);
    }
  }
  const xFraction = (clientX: number) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect?.width) return 0.5;
    const viewX = (clientX - rect.left) / rect.width * width;
    return Math.max(0, Math.min(1, (viewX - pad.left) / plotW));
  };
  const clientToSvg = (clientX: number, clientY: number) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return null;
    return {
      x: (clientX - rect.left) / rect.width * width,
      y: (clientY - rect.top) / rect.height * height,
    };
  };
  const isInsidePlot = (point: { x: number; y: number }) =>
    point.x >= pad.left && point.x <= pad.left + plotW
    && point.y >= pad.top && point.y <= pad.top + plotH;
  const pointAt = (clientX: number, clientY: number) => {
    const screenPoint = clientToSvg(clientX, clientY);
    if (!screenPoint || !isInsidePlot(screenPoint)) return null;
    const chartPoint = coordinateToChartPoint(
      screenPoint.x,
      screenPoint.y,
      span,
      plotGeometry,
    );
    return chartPoint ? { screenPoint, chartPoint } : null;
  };
  const updateAnnotation = (annotation: ChartAnnotation) => {
    dispatchAnnotation({ type: 'replace', annotation });
  };
  const annotationIndex = (time: string) =>
    span.findIndex(candle => candle?.time === time);
  const beginAnnotationDrag = (
    event: ReactPointerEvent<SVGElement>,
    annotation: ChartAnnotation,
    mode: AnnotationDragMode,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    const hit = pointAt(event.clientX, event.clientY);
    if (!hit) return;
    const startIndex = annotationIndex(annotation.start.time);
    const endIndex = annotationIndex(annotation.end.time);
    if (startIndex < 0 || endIndex < 0) return;
    dispatchAnnotation({ type: 'select', id: annotation.id });
    annotationDragRef.current = {
      pointerId: event.pointerId,
      mode,
      annotation,
      pointerStartIndex: hit.chartPoint.index,
      pointerStartPrice: hit.chartPoint.price,
      startIndex,
      endIndex,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    svgRef.current?.focus();
  };
  const updateAnnotationDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = annotationDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return false;
    const hit = pointAt(event.clientX, event.clientY);
    if (!hit) return true;
    const { annotation, mode } = drag;
    let next: ChartAnnotation = annotation;
    if (mode === 'move') {
      const maxDelta = Math.min(span.length - 1 - drag.startIndex, span.length - 1 - drag.endIndex);
      const minDelta = -Math.min(drag.startIndex, drag.endIndex);
      const delta = Math.max(minDelta, Math.min(
        maxDelta,
        hit.chartPoint.index - drag.pointerStartIndex,
      ));
      const startCandle = span[drag.startIndex + delta];
      const endCandle = span[drag.endIndex + delta];
      if (!startCandle || !endCandle) return true;
      const priceDelta = hit.chartPoint.price - drag.pointerStartPrice;
      next = {
        ...annotation,
        start: { time: startCandle.time, price: annotation.start.price + priceDelta },
        end: { time: endCandle.time, price: annotation.end.price + priceDelta },
      };
      if (annotation.kind === 'rectangle') {
        next = { ...next, ...normalizeRectangle(next.start, next.end) };
      }
    } else if (annotation.kind === 'line') {
      const point = { time: span[hit.chartPoint.index]?.time ?? hit.chartPoint.time, price: hit.chartPoint.price };
      next = {
        ...annotation,
        ...(mode === 'start' ? { start: point } : mode === 'end' ? { end: point } : {}),
      };
    } else {
      const time = span[hit.chartPoint.index]?.time ?? hit.chartPoint.time;
      const price = hit.chartPoint.price;
      const start = { ...annotation.start };
      const end = { ...annotation.end };
      if (mode === 'nw') { start.time = time; start.price = price; }
      if (mode === 'n') start.price = price;
      if (mode === 'ne') { end.time = time; start.price = price; }
      if (mode === 'e') end.time = time;
      if (mode === 'se') { end.time = time; end.price = price; }
      if (mode === 's') end.price = price;
      if (mode === 'sw') { start.time = time; end.price = price; }
      if (mode === 'w') start.time = time;
      next = { ...annotation, ...normalizeRectangle(start, end) };
    }
    updateAnnotation(next);
    return true;
  };
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      if (annotationDragRef.current) return;
      onZoomAt(xFraction(event.clientX), event.deltaY < 0 ? 0.8 : 1.25);
    };
    svg.addEventListener('wheel', handleWheel, { passive: false });
    return () => {
      svg.removeEventListener('wheel', handleWheel);
      if (ignoreClickTimerRef.current !== null) {
        window.clearTimeout(ignoreClickTimerRef.current);
      }
    };
  }, [onZoomAt]);
  const pointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    const target = event.target as Element;
    if (annotationDragRef.current || target.closest?.('[data-annotation-id]')) return;
    event.currentTarget.focus();
    if (annotationState.tool !== 'cursor') {
      const hit = pointAt(event.clientX, event.clientY);
      if (!hit) return;
      event.preventDefault();
      onInteraction();
      dispatchAnnotation({
        type: 'begin-draw',
        kind: annotationState.tool,
        point: { time: hit.chartPoint.time, price: hit.chartPoint.price },
      });
      annotationDrawRef.current = {
        pointerId: event.pointerId,
        kind: annotationState.tool,
        start: { time: hit.chartPoint.time, price: hit.chartPoint.price },
        current: { time: hit.chartPoint.time, price: hit.chartPoint.price },
        startX: hit.screenPoint.x,
        startY: hit.screenPoint.y,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
      return;
    }
    if (!target.closest?.('[data-annotation-id]')) {
      dispatchAnnotation({ type: 'select', id: null });
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    onInteraction();
    if (pointersRef.current.size === 1) {
      dragRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startViewStart: viewStart,
        viewSize,
        moved: false,
      };
    } else {
      dragRef.current = null;
      const points = [...pointersRef.current.values()];
      pinchRef.current = {
        distance: Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y),
      };
    }
  };
  const pointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const hit = pointAt(event.clientX, event.clientY);
    setCrosshair(showCrosshair ? hit?.chartPoint ?? null : null);
    const drawing = annotationDrawRef.current;
    if (drawing?.pointerId === event.pointerId) {
      if (hit) {
        drawing.current = { time: hit.chartPoint.time, price: hit.chartPoint.price };
        dispatchAnnotation({ type: 'update-draft', point: drawing.current });
      }
      return;
    }
    if (updateAnnotationDrag(event)) return;
    if (!pointersRef.current.has(event.pointerId)) return;
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const points = [...pointersRef.current.values()];
    if (points.length >= 2) {
      const distance = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
      if (pinchRef.current && distance > 0) {
        const factor = pinchRef.current.distance / distance;
        if (Math.abs(factor - 1) > 0.005) {
          onZoomAt(xFraction((points[0].x + points[1].x) / 2), factor);
          ignoreClickRef.current = true;
        }
      }
      pinchRef.current = { distance };
      return;
    }
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect?.width) return;
    const deltaX = drag.startX - event.clientX;
    if (Math.abs(deltaX) > 3) {
      drag.moved = true;
      ignoreClickRef.current = true;
    }
    onPanTo(drag.startViewStart + Math.round(deltaX / rect.width * (width / plotW) * drag.viewSize));
  };
  const pointerEnd = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drawing = annotationDrawRef.current;
    if (drawing?.pointerId === event.pointerId) {
      const hit = pointAt(event.clientX, event.clientY);
      if (hit) {
        drawing.current = { time: hit.chartPoint.time, price: hit.chartPoint.price };
        dispatchAnnotation({ type: 'update-draft', point: drawing.current });
      }
      const end = drawing.current;
      const endIndex = annotationIndex(end.time);
      const startIndex = annotationIndex(drawing.start.time);
      const moved = Math.hypot(
        (endIndex - startIndex) * step,
        y(end.price) - y(drawing.start.price),
      ) >= 3;
      annotationDrawRef.current = null;
      if (event.type === 'pointercancel') {
        dispatchAnnotation({ type: 'cancel-draw' });
        onDrawingTool('cursor');
      } else if (moved) {
        dispatchAnnotation({ type: 'finish-draw', id: newAnnotationId() });
      } else {
        dispatchAnnotation({ type: 'set-tool', tool: drawing.kind });
      }
      return;
    }
    if (annotationDragRef.current?.pointerId === event.pointerId) {
      if (event.type !== 'pointercancel') updateAnnotationDrag(event);
      annotationDragRef.current = null;
      return;
    }
    const drag = dragRef.current;
    const moved = Boolean(drag?.moved) || pointersRef.current.size > 1;
    pointersRef.current.delete(event.pointerId);
    if (pointersRef.current.size < 2) pinchRef.current = null;
    if (pointersRef.current.size === 1) {
      const [[pointerId, point]] = [...pointersRef.current.entries()];
      dragRef.current = {
        pointerId,
        startX: point.x,
        startViewStart: viewStart,
        viewSize,
        moved: false,
      };
    } else {
      dragRef.current = null;
    }
    if (moved) {
      if (ignoreClickTimerRef.current !== null) window.clearTimeout(ignoreClickTimerRef.current);
      ignoreClickTimerRef.current = window.setTimeout(() => {
        ignoreClickRef.current = false;
        ignoreClickTimerRef.current = null;
      }, 150);
    }
  };
  return <div className="chart-visual">
    <div className="chart-frame" data-testid="chart-replay">
    {!validCandles.length ? <div className="chart-empty">
      <span className="empty-crosshair">＋</span><strong>No candles in this window</strong>
      <span>Choose a date range that contains approved historical candles.</span>
    </div> : <svg
      ref={svgRef}
      className={`price-chart${annotationState.tool === 'cursor' ? ' cursor-mode' : ' drawing-mode'}`}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Historical ${symbol} candlestick replay chart`}
      tabIndex={0}
      onClick={event => event.currentTarget.focus()}
      onFocus={() => setChartFocused(true)}
      onBlur={() => setChartFocused(false)}
      onKeyDown={event => {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.currentTarget.blur();
          return;
        }
        if (annotationDragRef.current || annotationDrawRef.current) return;
        if (!replayReady || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
        event.preventDefault();
        onReplayStep(event.key === 'ArrowLeft' ? -1 : 1);
      }}
      onPointerDown={pointerDown}
      onPointerMove={pointerMove}
      onPointerUp={pointerEnd}
      onPointerCancel={pointerEnd}
      onPointerLeave={() => setCrosshair(null)}
    >
      <defs>
        <clipPath id={connectorClipPathId}>
          <rect x={pad.left} y={pad.top} width={plotW} height={plotH}/>
        </clipPath>
        <clipPath id={`${connectorClipPathId}-annotations`}>
          <rect x={pad.left} y={pad.top} width={plotW} height={plotH}/>
        </clipPath>
      </defs>
      {[0, 1, 2, 3, 4].map(i => {
        const py = pad.top + plotH * i / 4;
        const val = levels ? levels.high - (levels.high - levels.low) * i / 4 : 0;
        return <g key={i}>
          <line className="chart-grid" x1={pad.left} x2={width - pad.right} y1={py} y2={py}/>
          <text className="axis-label" x={width - pad.right + 12} y={py + 4}>{fmtPrice(val, symbol)}</text>
        </g>;
      })}
      {activeY !== null && <line
        className="current-price-line"
        x1={pad.left}
        x2={width - pad.right}
        y1={activeY}
        y2={activeY}
      />}
      <g
        className="swing-connectors"
        data-testid="swing-connectors"
        clipPath={`url(#${connectorClipPathId})`}
      >
        <SwingConnectorLayer
          segments={visibleSwingSegments}
          viewStart={viewStart}
          step={step}
          padLeft={pad.left}
          bounds={{
            left: pad.left,
            right: width - pad.right,
            top: pad.top,
            bottom: height - pad.bottom,
          }}
          yForPrice={y}
          formatPrice={price => fmtPrice(price, symbol)}
          renderTips={false}
        />
      </g>
      {span.map((candle, i) => {
        if (!candle) return null;
        const x = pad.left + step * (i + 0.5);
        const rising = candle.close > candle.open;
        const falling = candle.close < candle.open;
        const color = rising ? 'var(--up)' : falling ? 'var(--down)' : 'var(--trend-neutral)';
        const bodyY = Math.min(y(candle.open), y(candle.close));
        const bodyH = Math.max(3.2, Math.abs(y(candle.open) - y(candle.close)));
        const alert = markerMap.get(candle.time);
        const variantB = Boolean(alert?.alertType?.endsWith('-B'));
        const markerY = Math.max(pad.top + 8, y(candle.low) + 14);
        const hasVisibleAlert = Boolean(alert && (showAlertMarkers || showStrengthLabels));
        return <g key={candle.time} data-testid="chart-candle" className={hasVisibleAlert ? 'marker-group' : ''} onClick={() => {
          if (ignoreClickRef.current) {
            ignoreClickRef.current = false;
            return;
          }
          if (hasVisibleAlert && alert) onAlert(alert);
        }} role={hasVisibleAlert ? 'button' : undefined} tabIndex={hasVisibleAlert ? 0 : undefined} aria-label={hasVisibleAlert && alert ? `${alert.direction} ${alert.strengthLabel} alert at ${fmtTime(alert.time)}` : undefined} data-alert-id={hasVisibleAlert && alert ? alert.alertId : undefined} onKeyDown={e => { if (hasVisibleAlert && alert && (e.key === 'Enter' || e.key === ' ')) onAlert(alert); }}>
          <line x1={x} x2={x} y1={y(candle.high)} y2={y(candle.low)} stroke={color} strokeWidth="1.3"/>
          <rect x={x - Math.max(2, step * 0.31)} y={bodyY} width={Math.max(2.5, step * 0.62)} height={bodyH} fill={color}/>
          {hasVisibleAlert && alert && <g className={`chart-marker ${alert.direction.toLowerCase()}${variantB ? ' variant-b' : ''}`} data-testid={`chart-alert-${alert.alertId}`}>
            {showAlertMarkers && <>
              {variantB
                ? <rect x={x - 8} y={markerY - 8} width="16" height="16" rx="1"/>
                : <circle cx={x} cy={markerY} r="8"/>}
              <text x={x} y={markerY + 3} textAnchor="middle">{alert.direction === 'BUY' ? 'B' : 'S'}</text>
              {variantB && <text className="chart-marker-badge" x={x + 9} y={markerY - 7}>B</text>}
            </>}
            {showStrengthLabels && <text className="chart-marker-strength" x={x + (showAlertMarkers ? (variantB ? 18 : 10) : 0)} y={markerY + 3} textAnchor={showAlertMarkers ? 'start' : 'middle'}>{alert.strengthLabel.slice(0, 1)}</text>}
          </g>}
        </g>;
      })}
      {activeY !== null && <g>
        <rect className="price-tag" x={width - pad.right + 3} y={activeY - 10} width={pad.right - 6} height="20"/>
        <text className="price-tag-text" x={width - pad.right + 9} y={activeY + 4}>{fmtPrice(active?.close, symbol)}</text>
      </g>}
      {showTradeLevels && trades.filter(trade => trade.outcome === 'OPEN').map(trade => {
        const resolvedThrough = trade.exitIndex === null ? replayIndex : trade.exitIndex;
        const visibleFrom = Math.max(trade.entryIndex, viewStart);
        const visibleThrough = Math.min(resolvedThrough, viewStart + span.length - 1);
        if (visibleFrom > visibleThrough || visibleFrom > replayIndex) return null;
        const localFrom = visibleFrom - viewStart;
        const localThrough = visibleThrough - viewStart;
        const x1 = pad.left + step * (localFrom + 0.5);
        const x2 = pad.left + step * (localThrough + 0.5);
        return <g key={`trade-levels-${trade.tradeId}`} className="trade-level-set">
          <line className="trade-entry-line" x1={x1} x2={x2} y1={y(trade.entryPrice)} y2={y(trade.entryPrice)}/>
          <line className="trade-stop-line" x1={x1} x2={x2} y1={y(trade.stopPrice)} y2={y(trade.stopPrice)}/>
          <line className="trade-target-line" x1={x1} x2={x2} y1={y(trade.takeProfitPrice)} y2={y(trade.takeProfitPrice)}/>
        </g>;
      })}
      {focusedTrade && (() => {
        const from = Math.max(focusedTrade.entryIndex, viewStart);
        const through = Math.min(focusedTrade.exitIndex ?? replayIndex, viewStart + span.length - 1);
        if (from > through) return null;
        const coords = Array.from({ length: through - from + 1 }, (_, offset) => {
          const local = from + offset - viewStart;
          const candle = span[local];
          return candle ? `${pad.left + step * (local + .5)},${y(candle.close)}` : null;
        }).filter((point): point is string => Boolean(point));
        const x1 = pad.left + step * (from - viewStart + .5);
        const x2 = pad.left + step * (through - viewStart + .5);
        return <g className="journal-focus" data-testid="journal-trade-focus">
          <line className="trade-entry-line" x1={x1} x2={x2} y1={y(focusedTrade.entryPrice)} y2={y(focusedTrade.entryPrice)}/>
          <line className="trade-stop-line" x1={x1} x2={x2} y1={y(focusedTrade.stopPrice)} y2={y(focusedTrade.stopPrice)}/>
          <line className="trade-target-line" x1={x1} x2={x2} y1={y(focusedTrade.takeProfitPrice)} y2={y(focusedTrade.takeProfitPrice)}/>
          <polyline className="journal-price-path" points={coords.join(' ')}/>
          <line className="journal-entry-marker" x1={x1} x2={x1} y1={pad.top} y2={height - pad.bottom}/>
        </g>;
      })()}
      <g
        className="swing-connector-tips"
        data-testid="swing-connector-tips"
        clipPath={`url(#${connectorClipPathId})`}
        aria-hidden="true"
      >
        <SwingConnectorLayer
          segments={visibleSwingSegments}
          viewStart={viewStart}
          step={step}
          padLeft={pad.left}
          bounds={{
            left: pad.left,
            right: width - pad.right,
            top: pad.top,
            bottom: height - pad.bottom,
          }}
          yForPrice={y}
          formatPrice={price => fmtPrice(price, symbol)}
          renderLines={false}
        />
      </g>
      {visibleSwingEvents.map(event => {
        const localIndex = event.pivot_index - viewStart;
        const sourceOffset: Record<ConfirmedSwingEvent['source'], number> = {
          cct_minor: -1.5,
          cst_major: -0.5,
          dc: 0.5,
          hier: 1.5,
        };
        const offset = sourceOffset[event.source];
        const isHigh = event.type === 'swing_high';
        const markerX = pad.left + step * (localIndex + 0.5) + offset * 4;
        const markerY = Math.max(
          pad.top + 6,
          Math.min(
            height - pad.bottom - 6,
            y(event.price) + (isHigh ? -10 : 10) + offset * 5,
          ),
        );
        const family = event.source === 'cct_minor'
          ? 'MINOR' : event.source === 'cst_major' ? 'MAJOR' : event.source.toUpperCase();
        const side = isHigh ? 'HIGH' : 'LOW';
        const label = isHigh ? 'H' : 'L';
        return <g
          key={`${event.source}-${event.type}-${event.pivot_index}-${event.confirmed_index}`}
          className="swing-marker"
          data-source={event.source}
          data-testid={`swing-marker-${event.source}-${event.pivot_index}-${event.confirmed_index}`}
          aria-label={`${family} ${side} ${fmtPrice(event.price, symbol)}`}
        >
          <title>{`${family} ${side} ${fmtPrice(event.price, symbol)} · pivot ${fmtTime(event.pivot_time)} · confirmed ${fmtTime(event.confirmed_time)}`}</title>
          <circle cx={markerX} cy={markerY} r="6"/>
          <text x={markerX} y={markerY + 2.5} textAnchor="middle">{label}</text>
        </g>;
      })}
      {span.map((candle, index) => ({ candle, index })).filter(({ candle, index }) =>
        candle && axisLabelIndexes.has(index),
      ).map(({ candle: c, index: i }) => {
        if (!c) return null;
        const label = axisTimeLabel(c.time);
        return <text key={c.time} className="axis-label x-label" x={pad.left + step * (i + 0.5)} y={height - 10} textAnchor="middle">{label}</text>;
      })}
      <g clipPath={`url(#${connectorClipPathId}-annotations)`}>
        <DrawingLayer
          annotations={annotationState.annotations}
          draft={annotationState.draft}
          selectedId={annotationState.selectedId}
          candles={span}
          geometry={plotGeometry}
          symbol={symbol}
          formatTime={fmtTime}
          formatPrice={fmtPrice}
          showPrices={showAnnotationPrices}
          onSelect={id => dispatchAnnotation({ type: 'select', id })}
          onDelete={id => dispatchAnnotation({ type: 'delete', id })}
          onDragStart={beginAnnotationDrag}
        />
      </g>
      {showCrosshair && crosshair && <g className="chart-crosshair" data-testid="chart-crosshair" pointerEvents="none">
        <line className="crosshair-line" x1={crosshair.x} x2={crosshair.x} y1={pad.top} y2={pad.top + plotH} stroke="var(--text-muted)" strokeDasharray="3 4" opacity="0.55"/>
        <line className="crosshair-line" x1={pad.left} x2={pad.left + plotW} y1={crosshair.y} y2={crosshair.y} stroke="var(--text-muted)" strokeDasharray="3 4" opacity="0.55"/>
        <rect
          className="crosshair-axis-tag"
          x={Math.max(pad.left, Math.min(crosshair.x - 45, pad.left + plotW - 90))}
          y={height - pad.bottom + 7}
          width="90"
          height="19"
          rx="2"
        />
        <text
          className="crosshair-axis-label"
          x={Math.max(pad.left, Math.min(crosshair.x - 45, pad.left + plotW - 90)) + 45}
          y={height - pad.bottom + 20}
          textAnchor="middle"
          data-testid="crosshair-time-label"
        >{axisTimeLabel(crosshair.time)}</text>
        <rect
          className="crosshair-axis-tag"
          x={width - pad.right + 3}
          y={Math.max(pad.top, Math.min(crosshair.y - 9, pad.top + plotH - 18))}
          width={pad.right - 6}
          height="18"
          rx="2"
        />
        <text
          className="crosshair-axis-label"
          x={width - pad.right + 8}
          y={Math.max(pad.top, Math.min(crosshair.y - 9, pad.top + plotH - 18)) + 12}
          data-testid="crosshair-price-label"
        >{fmtPrice(crosshair.price, symbol)}</text>
      </g>}
    </svg>}
    {flatPriceWindow && <div className="chart-flat-notice" role="status" data-testid="status-flat-market">
      No price movement in this visible slice. Replay is continuing through flat historical candles.
    </div>}
    </div>
    {validCandles.length > 0 && hasTrendTracks && <div className="trend-track-strip" data-testid="indicator-track-strip">
      <svg
        viewBox={`0 0 ${width} 48`}
        role="img"
        aria-label="Indicator states aligned with candle times"
        data-testid="indicator-track-chart"
      >
        {span.map((candle, i) => {
          if (!candle) return null;
          const x = pad.left + step * i;
          return <g key={`trend-${candle.time}`} data-testid="trend-overlay-candle">
            {showCct && <rect data-testid="cct-state-band" x={x} y="4" width={Math.max(1, step - 0.7)} height="4" fill={cctColor(candle.cct)}>
              <title>CCT {candle.cct ?? 'unavailable'} at {fmtTime(candle.time)}</title>
            </rect>}
            {showCst && <rect data-testid="cst-state-band" x={x} y="12" width={Math.max(1, step - 0.7)} height="4" fill={cstColor(candle.cst)}>
              <title>CST {candle.cst ?? 'unavailable'} at {fmtTime(candle.time)}</title>
            </rect>}
            {showWickPressure && candle.wickPressure && <rect data-testid="wick-pressure-state-band" x={x} y="20" width={Math.max(1, step - 0.7)} height="4" fill={wickPressureColor(candle.wickPressure)}>
              <title>Wick pressure {wickPressureLabel(candle.wickPressure)} at {fmtTime(candle.time)}</title>
            </rect>}
            {showDc && <rect data-testid="dc-state-band" x={x} y="28" width={Math.max(1, step - 0.7)} height="4" fill={cstColor(candle.dcState)}>
              <title>DC shadow {candle.dcState ?? 'unavailable'} at {fmtTime(candle.time)}</title>
            </rect>}
            {showHier && <rect data-testid="hier-state-band" x={x} y="36" width={Math.max(1, step - 0.7)} height="4" fill={cstColor(candle.hierState)}>
              <title>HIER shadow {candle.hierState ?? 'unavailable'} at {fmtTime(candle.time)}</title>
            </rect>}
          </g>;
        })}
        {showCct && <text className="axis-label trend-track-label" x={width - pad.right + 12} y="8">CCT</text>}
        {showCst && <text className="axis-label trend-track-label" x={width - pad.right + 12} y="16">CST</text>}
        {showWickPressure && <text className="axis-label trend-track-label" x={width - pad.right + 12} y="24">WP</text>}
        {showDc && <text className="axis-label trend-track-label" x={width - pad.right + 12} y="32">DC</text>}
        {showHier && <text className="axis-label trend-track-label" x={width - pad.right + 12} y="40">HIER</text>}
      </svg>
    </div>}
  </div>;
}

function defaultDateWindow(symbol: typeof INSTRUMENT_SYMBOLS[number]) {
  const year = symbol === 'AUDJPY' ? '2024' : '2023';
  return {
    from: `${year}-01-01`,
    to: `${year}-01-31`,
  };
}

export function Home() {
  const [selectedSymbol, setSelectedSymbol] = useState<typeof INSTRUMENT_SYMBOLS[number]>('XAUUSD');
  const [lotSizesBySymbol, setLotSizesBySymbol] = useState<Record<InstrumentSymbol, number>>({
    ...DEFAULT_LOT_SIZES,
  });
  const dataset = useGetLabDataset({ symbol: selectedSymbol });
  const evolvePolicy = useGetEvolvePolicy({
    query: { staleTime: 60_000 },
  });
  const activeDataset = dataset.data?.symbol === selectedSymbol ? dataset.data : undefined;
  const [knownInstrumentReviews, setKnownInstrumentReviews] = useState<InstrumentReviewInfo[]>([]);
  const incomingInstrumentReviews = dataset.data?.instrumentReviews;
  const instrumentReviews = incomingInstrumentReviews?.length
    ? incomingInstrumentReviews : knownInstrumentReviews;
  useEffect(() => {
    if (incomingInstrumentReviews?.length) setKnownInstrumentReviews(incomingInstrumentReviews);
  }, [incomingInstrumentReviews]);
  const [timeframe, setTimeframe] = useState<Timeframe>('M15');
  const [from, setFrom] = useState(() => defaultDateWindow('XAUUSD').from);
  const [to, setTo] = useState(() => defaultDateWindow('XAUUSD').to);
  const [viewMonth, setViewMonth] = useState('2023-01');
  const [replaySession, setReplaySession] = useState<ReplaySessionStartResult | null>(null);
  const [simulationSettings, setSimulationSettings] = useState<SimulationSettings>({
    enabled: true, startingBalance: 10000, riskPercent: 1, sizingMode: 'LOTS', lotSize: DEFAULT_LOT_SIZES.XAUUSD, rewardMultiple: 2,
    exitModel: 'candle_geometry',
    stopBodyWeight: 1,
    stopWickMultiplier: 2,
    targetRangeMultiplier: 3,
  });
  const [simulationSnapshot, setSimulationSnapshot] = useState<SimulationSnapshot | null>(null);
  const [simulatedTrades, setSimulatedTrades] = useState<SimulatedTrade[]>([]);
  const [policySimulationSnapshot, setPolicySimulationSnapshot] = useState<SimulationSnapshot | null>(null);
  const [policySimulatedTrades, setPolicySimulatedTrades] = useState<SimulatedTrade[]>([]);
  const [policyDecisions, setPolicyDecisions] = useState<Map<string, ReplayPolicyDecision>>(() => new Map());
  const [currentReplayCandle, setCurrentReplayCandle] = useState<Candle | null>(null);
  const [streamStates, setStreamStates] = useState<Map<number, ReplayCandleState>>(() => new Map());
  const [streamWickPressure, setStreamWickPressure] = useState<Map<number, WickPressure>>(() => new Map());
  const [streamAlerts, setStreamAlerts] = useState<ReplayAlert[]>([]);
  const [streamSwingEvents, setStreamSwingEvents] = useState<ConfirmedSwingEvent[]>([]);
  const [computedThrough, setComputedThrough] = useState(-1);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try {
      return window.localStorage.getItem('forex-strategy-lab-theme') === 'light' ? 'light' : 'dark';
    } catch {
      return 'dark';
    }
  });
  const [direction, setDirection] = useState('ALL');
  const [strength, setStrength] = useState('ALL');
  const [engulfingOnly, setEngulfingOnly] = useState(false);
  const [variantFilter, setVariantFilter] = useState<'ALL' | 'A' | 'B'>('ALL');
  const [wickPressureFilter, setWickPressureFilter] = useState<'ALL' | WickPressure>('ALL');
  const [mtfAgreementOnly, setMtfAgreementOnly] = useState(false);
  const [aiFilterEnabled, setAiFilterEnabled] = useState(false);
  const [alertSort, setAlertSort] = useState<{ key: AlertSortKey; descending: boolean }>({
    key: 'time',
    descending: true,
  });
  const [showCct, setShowCct] = useState(true);
  const [showCst, setShowCst] = useState(true);
  const [showWickPressure, setShowWickPressure] = useState(false);
  const [showDc, setShowDc] = useState(true);
  const [showHier, setShowHier] = useState(true);
  const [showSwings, setShowSwings] = useState(false);
  const [swingFamilies, setSwingFamilies] = useState<SwingFamilySettings>({
    MAJOR: true,
    MINOR: true,
    DC: true,
    HIER: true,
  });
  const [showAlertMarkers, setShowAlertMarkers] = useState(true);
  const [showStrengthLabels, setShowStrengthLabels] = useState(true);
  const [showCrosshair, setShowCrosshair] = useState(true);
  const [showAnnotationPrices, setShowAnnotationPrices] = useState(true);
  const [showTradeLevels, setShowTradeLevels] = useState(true);
  const [drawingTool, setDrawingTool] = useState<AnnotationTool | null>('cursor');
  const [focusedTrade, setFocusedTrade] = useState<SimulatedTrade | null>(null);
  const [rightPanel, setRightPanel] = useState<'alerts' | 'journal'>('alerts');
  const [selected, setSelected] = useState<Alert | null>(null);
  const [comment, setComment] = useState<AICommentResult | null>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const [scrubPosition, setScrubPosition] = useState(0);
  const [chartViewSize, setChartViewSize] = useState(100);
  const [chartStart, setChartStart] = useState<number | null>(null);
  const [followLatest, setFollowLatest] = useState(true);
  const [monthChunkState, setMonthChunkState] = useState<{
    windowKey: string;
    chunks: CandleMonthChunk[];
    totalCandles: number;
  } | null>(null);
  const [loadedMonthChunks, setLoadedMonthChunks] = useState(
    () => new Map<string, { offset: number; candles: Candle[] }>(),
  );
  const initializedMonthWindow = useRef('');
  const resumeAfterScrub = useRef(false);
  const scrubPositionRef = useRef(0);
  const scrubbingRef = useRef(false);
  const replayGenerationRef = useRef(0);
  const replaySessionRef = useRef<ReplaySessionStartResult | null>(null);
  const policyVersionRef = useRef<number | null>(null);
  const cursorRef = useRef(0);
  const chartStartRef = useRef(0);
  const chartViewSizeRef = useRef(100);
  const advanceInFlightRef = useRef(false);
  const advanceToRef = useRef<(targetIndex: number) => Promise<boolean>>(async () => false);
  const startReplayMutation = useStartLabReplaySession();
  const advanceReplayMutation = useAdvanceLabReplaySession();
  const commentHook = useGetAlertComment();
  const displayTo = activeDataset?.endDate.slice(0, 10) ?? to;
  const monthWindowKey = `${selectedSymbol}:${timeframe}:${from}:${displayTo}`;
  const storedMonthChunkState = monthChunkState?.windowKey === monthWindowKey
    ? monthChunkState : null;
  const storedMonthChunks = storedMonthChunkState?.chunks ?? [];
  const chunkForCursor = storedMonthChunks.find(chunk =>
    cursor >= chunk.startIndex && cursor < chunk.startIndex + chunk.count,
  );
  const cachedMonthCandle = (index: number) => Array.from(loadedMonthChunks.entries())
    .some(([key, cached]) => key.startsWith(`${monthWindowKey}:`)
      && index >= cached.offset && index < cached.offset + cached.candles.length);
  const hasReplaySessionForWindow = replaySession?.timeframe === timeframe
    && replaySession.symbol === selectedSymbol
    && replaySession.from === from && replaySession.to === to
    ? replaySession : null;
  const anticipatedChunk = storedMonthChunks.find(chunk => chunk.month === viewMonth);
  const anticipatedDisplayEnd = anticipatedChunk
    ? anticipatedChunk.startIndex + anticipatedChunk.count - 1
    : -1;
  const anticipatedViewSize = Math.min(chartViewSize, Math.max(1, anticipatedDisplayEnd + 1));
  const anticipatedMaxStart = Math.max(0, anticipatedDisplayEnd - anticipatedViewSize + 1);
  const anticipatedChartStart = Math.max(0, Math.min(chartStart ?? anticipatedMaxStart, anticipatedMaxStart));
  const anticipatedChartEnd = Math.min(anticipatedDisplayEnd, anticipatedChartStart + anticipatedViewSize - 1);
  const missingChartChunk = storedMonthChunks.find(chunk =>
    chunk.startIndex <= anticipatedChartEnd
    && chunk.startIndex + chunk.count - 1 >= anticipatedChartStart
    && !loadedMonthChunks.has(`${monthWindowKey}:${chunk.month}`),
  );
  const cursorCandleCached = cachedMonthCandle(cursor);
  const requestedChunkMonth = missingChartChunk?.month
    ?? (!cursorCandleCached ? chunkForCursor?.month : undefined)
    ?? viewMonth;
  const params = {
    symbol: selectedSymbol, timeframe, from, to: displayTo, limit: 100000,
    chunkMonth: requestedChunkMonth,
  };
  const candlesQuery = useGetLabCandles(params, {
    query: { enabled: Boolean(activeDataset), queryKey: getGetLabCandlesQueryKey(params) },
  });
  const simulationParams = {
    symbol: selectedSymbol,
    timeframe,
    from,
    to,
    limit: 100000,
    chunkMonth: to.slice(0, 7),
  };
  const simulationCandlesQuery = useGetLabCandles(simulationParams, {
    query: {
      enabled: Boolean(activeDataset && from && to && from <= to),
      queryKey: getGetLabCandlesQueryKey(simulationParams),
    },
  });
  const candleData = candlesQuery.data?.timeframe === timeframe
    && candlesQuery.data.symbol === selectedSymbol
    && candlesQuery.data.from === from && candlesQuery.data.to === displayTo
    && candlesQuery.data.chunkMonth === requestedChunkMonth
    ? candlesQuery.data : undefined;
  const simulationWindowData = simulationCandlesQuery.data?.timeframe === timeframe
    && simulationCandlesQuery.data.symbol === selectedSymbol
    && simulationCandlesQuery.data.from === from
    && simulationCandlesQuery.data.to === to
    ? simulationCandlesQuery.data : undefined;
  const currentMonthChunkState = storedMonthChunkState ?? (
    candleData
      ? { windowKey: monthWindowKey, chunks: candleData.chunks, totalCandles: candleData.totalCandles }
      : null
  );
  const currentMonthChunks = currentMonthChunkState?.chunks ?? [];
  const activeMonthChunk = currentMonthChunks.find(chunk => chunk.month === viewMonth)
    ?? chunkForCursor;
  const matchingSession = hasReplaySessionForWindow;
  const dataTotalCandles = currentMonthChunkState?.totalCandles ?? candleData?.totalCandles ?? 0;
  const simulationTotalCandles = matchingSession?.totalCandles
    ?? simulationWindowData?.totalCandles ?? 0;
  const totalCandles = matchingSession ? simulationTotalCandles : dataTotalCandles;
  const m1DataReplay = timeframe === 'M1' && !matchingSession && Boolean(totalCandles);
  const replayReady = Boolean(matchingSession && simulationTotalCandles && activeDataset?.ownerApproved);
  const playbackReady = replayReady || (timeframe === 'M1' && m1DataReplay);
  const getCandleAt = (index: number): Candle | undefined => {
    if (!currentMonthChunkState || index < 0 || index >= currentMonthChunkState.totalCandles) return undefined;
    const chunk = currentMonthChunks.find(item =>
      index >= item.startIndex && index < item.startIndex + item.count,
    );
    if (!chunk) return undefined;
    const cached = loadedMonthChunks.get(`${monthWindowKey}:${chunk.month}`);
    if (cached && index >= cached.offset && index < cached.offset + cached.candles.length) {
      return cached.candles[index - cached.offset];
    }
    if (
      candleData?.chunkMonth === chunk.month
      && index >= candleData.chunkOffset
      && index < candleData.chunkOffset + candleData.candles.length
    ) {
      return candleData.candles[index - candleData.chunkOffset];
    }
    return undefined;
  };
  const displayEnd = activeMonthChunk
    ? activeMonthChunk.startIndex + activeMonthChunk.count - 1
    : -1;
  // Keep candle spacing fixed while navigating the full display window.
  const visibleViewSize = replayReady
    ? chartViewSize
    : Math.min(chartViewSize, Math.max(1, displayEnd + 1));
  const maxChartStart = Math.max(0, displayEnd - visibleViewSize + 1);
  const chartStartIndex = Math.max(0, Math.min(chartStart ?? maxChartStart, maxChartStart));
  const chartEndIndex = displayEnd >= chartStartIndex
    ? Math.min(displayEnd, chartStartIndex + visibleViewSize - 1)
    : chartStartIndex - 1;
  const rawChartCandles: Array<Candle | undefined> = chartEndIndex >= chartStartIndex
    ? Array.from({ length: chartEndIndex - chartStartIndex + 1 }, (_, offset) =>
        getCandleAt(chartStartIndex + offset),
      )
    : [];
  const chartCandles = rawChartCandles.map((candle, offset) => {
    if (!candle) return undefined;
    const index = chartStartIndex + offset;
    const state = matchingSession ? streamStates.get(index) : undefined;
    const wickPressure = matchingSession ? streamWickPressure.get(index) : undefined;
    return state || wickPressure
      ? {
          ...candle,
          ...(state ? {
            cct: state.cct,
            cst: state.cst,
            dcState: state.dcState,
            hierState: state.hierState,
          } : {}),
          ...(wickPressure ? { wickPressure } : {}),
        }
      : candle;
  });
  const browseIndex = activeMonthChunk
    ? activeMonthChunk.startIndex + activeMonthChunk.count - 1
    : cursor;
  const activeCandle = matchingSession && cursor < simulationTotalCandles
    ? currentReplayCandle
    : getCandleAt(m1DataReplay || replayReady ? cursor : browseIndex) ?? null;
  const monthProgress = `Month ${monthOrdinal(viewMonth)}/36`;
  const symbolDataLoading = !activeDataset && (dataset.isLoading || dataset.isFetching);
  const candleWindowLoading = Boolean(activeDataset)
    && candlesQuery.isFetching && !candleData && !chartCandles.some(Boolean);
  const chartLoadingMessage = symbolDataLoading
    ? `Loading ${selectedSymbol} market data…`
    : candleWindowLoading
      ? `Loading ${selectedSymbol} ${timeframe} candles…`
      : null;
  const loadStatus = symbolDataLoading
    ? `Loading ${selectedSymbol} market data…`
    : candlesQuery.isFetching
      ? `Loading ${monthProgress} · ${requestedChunkMonth}…`
      : candleWindowLoading
        ? `Loading ${selectedSymbol} ${timeframe} candles…`
      : null;
  const resetMonthChunks = () => {
    initializedMonthWindow.current = '';
    setMonthChunkState(null);
    setLoadedMonthChunks(new Map());
  };
  const clearReplay = () => {
    replayGenerationRef.current += 1;
    replaySessionRef.current = null;
    cursorRef.current = 0;
    setReplaySession(null);
    setSimulationSnapshot(null);
    setSimulatedTrades([]);
    setPolicySimulationSnapshot(null);
    setPolicySimulatedTrades([]);
    setPolicyDecisions(new Map());
    setCurrentReplayCandle(null);
    setStreamStates(new Map());
    setStreamWickPressure(new Map());
    setStreamAlerts([]);
    setStreamSwingEvents([]);
    setComputedThrough(-1);
    setCursor(0);
    setPlaying(false);
    setChartStart(null);
    setFollowLatest(true);
    setSelected(null);
    setComment(null);
    resetMonthChunks();
  };
  useEffect(() => {
    const version = evolvePolicy.data?.version;
    if (version === undefined) return;
    if (policyVersionRef.current !== null && policyVersionRef.current !== version) {
      clearReplay();
    }
    policyVersionRef.current = version;
  }, [evolvePolicy.data?.version]);
  const selectSymbol = (symbol: typeof selectedSymbol) => {
    if (symbol === selectedSymbol) return;
    if (!instrumentReviews.some(review => review.symbol === symbol && review.ownerApproved)) return;
    clearReplay();
    persistSessionSymbol(symbol);
    const dateWindow = defaultDateWindow(symbol);
    setFrom(dateWindow.from);
    setTo(dateWindow.to);
    setViewMonth(dateWindow.from.slice(0, 7));
    setSimulationSettings(current => ({
      ...current,
      lotSize: lotSizesBySymbol[symbol] ?? DEFAULT_LOT_SIZES[symbol],
    }));
    setSelectedSymbol(symbol);
  };
  useEffect(() => {
    if (!activeDataset) return;
    const start = activeDataset.startDate.slice(0, 10);
    const end = activeDataset.endDate.slice(0, 10);
    setFrom(current => current < start || current > end ? start : current);
    setTo(current => current < start || current > end ? end : current);
    const firstMonth = start.slice(0, 7);
    const lastMonth = end.slice(0, 7);
    if (viewMonth < firstMonth || viewMonth > lastMonth) setViewMonth(firstMonth);
  }, [activeDataset, viewMonth]);
  useEffect(() => {
    if (!activeDataset || activeDataset.ownerApproved) return;
    const fallback = instrumentReviews.find(review => review.ownerApproved)?.symbol;
    const nextSymbol = fallback && INSTRUMENT_SYMBOLS.includes(fallback as typeof selectedSymbol)
      ? fallback as typeof selectedSymbol
      : 'XAUUSD';
    if (nextSymbol === selectedSymbol) return;
    clearReplay();
    persistSessionSymbol(nextSymbol);
    setSimulationSettings(current => ({
      ...current,
      lotSize: lotSizesBySymbol[nextSymbol] ?? DEFAULT_LOT_SIZES[nextSymbol],
    }));
    setSelectedSymbol(nextSymbol);
  }, [activeDataset, instrumentReviews, selectedSymbol, lotSizesBySymbol]);
  const beginScrub = () => {
    if (scrubbingRef.current) return;
    scrubbingRef.current = true;
    resumeAfterScrub.current = playing;
    scrubPositionRef.current = cursor;
    setScrubPosition(cursor);
    setScrubbing(true);
  };
  const endScrub = () => {
    if (!scrubbingRef.current) return;
    scrubbingRef.current = false;
    const target = scrubPositionRef.current;
    const resume = resumeAfterScrub.current;
    setScrubbing(false);
    setScrubPosition(target);
    resumeAfterScrub.current = false;
    void (async () => {
      const moved = await stepTo(target);
      if (resume && moved) setPlaying(true);
    })();
  };
  useEffect(() => {
    const data = candleData;
    if (
      !data
      || data.timeframe !== timeframe
      || data.from !== from
      || data.to !== displayTo
      || !data.chunkMonth
    ) return;
    const key = `${monthWindowKey}:${data.chunkMonth}`;
    setLoadedMonthChunks(previous => {
      if (previous.has(key)) return previous;
      return new Map(previous).set(key, {
        offset: data.chunkOffset,
        candles: data.candles,
      });
    });
    setMonthChunkState(previous => {
      if (previous?.windowKey === monthWindowKey) return previous;
      return {
        windowKey: monthWindowKey,
        chunks: data.chunks,
        totalCandles: data.totalCandles,
      };
    });
    if (initializedMonthWindow.current !== monthWindowKey) {
      initializedMonthWindow.current = monthWindowKey;
      if (!replaySessionRef.current) {
        const initialChunk = data.chunks.find(chunk => chunk.month === viewMonth)
          ?? data.chunks[0];
        const initialIndex = initialChunk?.startIndex ?? 0;
        cursorRef.current = initialIndex;
        setCursor(initialIndex);
      }
      setPlaying(false);
    }
  }, [timeframe, from, displayTo, monthWindowKey, candleData, viewMonth]);
  useEffect(() => {
    if (!focusedTrade || !candleData) return;
    const entryMonth = focusedTrade.entryTime.slice(0, 7);
    const resolutionMonth = (focusedTrade.exitTime ?? focusedTrade.entryTime).slice(0, 7);
    if (resolutionMonth !== entryMonth && candleData.chunkMonth === resolutionMonth && viewMonth !== entryMonth) {
      setViewMonth(entryMonth);
    }
  }, [focusedTrade, candleData, viewMonth]);
  useEffect(() => {
    try {
      window.localStorage.setItem('forex-strategy-lab-theme', theme);
    } catch {
      // Keep the chosen theme for this session when storage is unavailable.
    }
  }, [theme]);
  useEffect(() => {
    chartViewSizeRef.current = chartViewSize;
  }, [chartViewSize]);
  useEffect(() => {
    chartStartRef.current = chartStartIndex;
  }, [chartStartIndex]);
  useEffect(() => {
    if (followLatest) {
      chartStartRef.current = maxChartStart;
      setChartStart(maxChartStart);
      return;
    }
    setChartStart(current => {
      const clamped = Math.min(current ?? maxChartStart, maxChartStart);
      chartStartRef.current = clamped;
      return clamped;
    });
  }, [followLatest, maxChartStart]);
  const boundsStart = activeDataset?.startDate.slice(0, 10) ?? '2023-01-01';
  const boundsEnd = activeDataset?.endDate.slice(0, 10) ?? '2025-12-31';
  const validDates = Boolean(from && to && from <= to && from >= boundsStart && to <= boundsEnd);
  const revealedAlerts = useMemo(() => streamAlerts
    .filter(item => item.candleIndex <= cursor)
    .map(item => item.alert), [streamAlerts, cursor]);
  const displayedRevealedAlerts = useMemo(() => applyAiPolicyView(
    streamAlerts.filter(item => item.candleIndex <= cursor),
    aiFilterEnabled,
    evolvePolicy.data?.mode,
    policyDecisions,
  ).visible.map(item => item.alert), [
    streamAlerts,
    cursor,
    aiFilterEnabled,
    evolvePolicy.data?.mode,
    policyDecisions,
  ]);
  const chartAlerts = useMemo(() => {
    const items = streamAlerts.filter(item =>
      item.candleIndex >= chartStartIndex &&
      item.candleIndex <= chartEndIndex &&
      item.candleIndex <= cursor);
    return applyAiPolicyView(
      items,
      aiFilterEnabled,
      evolvePolicy.data?.mode,
      policyDecisions,
    ).visible.map(item => item.alert);
  }, [
    streamAlerts,
    chartStartIndex,
    chartEndIndex,
    cursor,
    aiFilterEnabled,
    evolvePolicy.data?.mode,
    policyDecisions,
  ]);
  const sortedAlertItems = useMemo<AlertTapeItem[]>(() => streamAlerts
    .map(item => ({
      ...item,
      alert: item.alert as MtfAlert,
      wpState: streamWickPressure.get(item.candleIndex),
    }))
    .filter(({ alert, wpState }) =>
      (direction === 'ALL' || alert.direction === direction) &&
      (strength === 'ALL' || alert.strengthLabel === strength) &&
      (!engulfingOnly || alert.engulfing) &&
      (variantFilter === 'ALL' ||
        (alert.alertType?.endsWith('-B') ? 'B' : 'A') === variantFilter) &&
      (wickPressureFilter === 'ALL' || wpState === wickPressureFilter) &&
      (!mtfAgreementOnly || timeframe !== 'M5' || alert.mtfTrendAgree === true))
    .sort((left, right) => {
      const variant = (item: AlertTapeItem) =>
        item.alert.alertType?.endsWith('-B') ? 'B' : 'A';
      let comparison = 0;
      if (alertSort.key === 'time') {
        comparison = Date.parse(left.alert.time) - Date.parse(right.alert.time);
      } else if (alertSort.key === 'wp') {
        comparison = (left.wpState ?? '').localeCompare(right.wpState ?? '');
      } else if (alertSort.key === 'engulf') {
        comparison = Number(Boolean(left.alert.engulfing)) - Number(Boolean(right.alert.engulfing));
      } else {
        comparison = variant(left).localeCompare(variant(right));
      }
      if (!comparison) {
        comparison = Date.parse(left.alert.time) - Date.parse(right.alert.time);
      }
      if (!comparison) {
        comparison = left.alert.alertId.localeCompare(right.alert.alertId);
      }
      return alertSort.descending ? -comparison : comparison;
    }), [
      streamAlerts,
      streamWickPressure,
      cursor,
      direction,
      strength,
      engulfingOnly,
      variantFilter,
      wickPressureFilter,
      mtfAgreementOnly,
      alertSort,
    ]);
  const currentManualAlertItems = useMemo(
    () => sortedAlertItems.filter(item => item.candleIndex <= cursor),
    [sortedAlertItems, cursor],
  );
  const alertTapePolicyView = useMemo(
    () => applyAiPolicyView(
      currentManualAlertItems,
      aiFilterEnabled,
      evolvePolicy.data?.mode,
      policyDecisions,
    ),
    [
      currentManualAlertItems,
      aiFilterEnabled,
      evolvePolicy.data?.mode,
      policyDecisions,
    ],
  );
  const shownAlertItems = alertTapePolicyView.visible;
  const hiddenByAiFilter = alertTapePolicyView.hidden;
  const visibleSimulatedTrades = useMemo(() => simulatedTrades
    .filter(trade => trade.entryIndex <= cursor)
    .map(trade => trade.exitIndex !== null && trade.exitIndex > cursor
      ? {
          ...trade,
          exitIndex: null,
          exitTime: null,
          exitPrice: null,
          outcome: 'OPEN' as const,
          exitReason: null,
          grossPnl: 0,
          cost: 0,
          netPnl: 0,
          realizedR: 0,
          netR: 0,
        }
      : trade), [simulatedTrades, cursor]);
  const policySimulationActive = aiFilterEnabled && evolvePolicy.data?.mode === 'active';
  const displayedSimulationSnapshot = policySimulationActive
    ? policySimulationSnapshot
    : simulationSnapshot;
  const displayedSimulatedTrades = policySimulationActive
    ? policySimulatedTrades
    : visibleSimulatedTrades;
  const filteredAlertStats = useMemo(() => {
    const alertIds = new Set(shownAlertItems.map(item => item.alert.alertId));
    const closedTrades = displayedSimulatedTrades.filter(trade =>
      alertIds.has(trade.alertId) && trade.outcome !== 'OPEN');
    const wins = closedTrades.filter(trade => trade.outcome === 'WIN').length;
    return {
      closedTradeCount: closedTrades.length,
      winRatePct: closedTrades.length ? (wins / closedTrades.length) * 100 : null,
      averageR: closedTrades.length
        ? closedTrades.reduce((total, trade) => total + trade.realizedR, 0) / closedTrades.length
        : null,
      expectancyAfterCosts: closedTrades.length
        ? closedTrades.reduce((total, trade) => total + trade.netR, 0) / closedTrades.length
        : null,
      totalPnlAfterCosts: closedTrades.reduce((total, trade) => total + trade.netPnl, 0),
    };
  }, [shownAlertItems, displayedSimulatedTrades]);
  const changeAlertSort = (key: AlertSortKey) => setAlertSort(current =>
    current.key === key
      ? { ...current, descending: !current.descending }
      : { key, descending: key === 'time' });
  const sortButton = (key: AlertSortKey, label: string) => (
    <button
      type="button"
      className={`alert-sort-button${alertSort.key === key ? ' active' : ''}`}
      aria-label={`Sort alerts by ${label}${alertSort.key === key ? (alertSort.descending ? ' descending' : ' ascending') : ''}`}
      aria-pressed={alertSort.key === key}
      onClick={() => changeAlertSort(key)}
      data-testid={`sort-alert-by-${key}`}
    >
      {label}{alertSort.key === key ? (alertSort.descending ? ' ↓' : ' ↑') : ''}
    </button>
  );
  const revealedCounts = useMemo(() => ({
    buy: displayedRevealedAlerts.filter(a => a.direction === 'BUY').length,
    sell: displayedRevealedAlerts.filter(a => a.direction === 'SELL').length,
    strong: displayedRevealedAlerts.filter(a => a.strengthLabel === 'STRONG').length,
    neutral: displayedRevealedAlerts.filter(a => a.strengthLabel === 'NEUTRAL').length,
    weak: displayedRevealedAlerts.filter(a => a.strengthLabel === 'WEAK').length,
  }), [displayedRevealedAlerts]);
  const advanceTo = useCallback(async (targetIndex: number): Promise<boolean> => {
    const session = replaySessionRef.current;
    if (!session || advanceInFlightRef.current) return false;
    const generation = replayGenerationRef.current;
    const target = Math.max(0, Math.min(session.totalCandles - 1, Math.round(targetIndex)));
    advanceInFlightRef.current = true;
    let response: Awaited<ReturnType<typeof advanceReplayMutation.mutateAsync>> | null = null;
    const receivedStates: ReplayCandleState[] = [];
    const receivedWickPressureStates: WickPressurePoint[] = [];
    const receivedAlerts: ReplayAlert[] = [];
    const receivedPolicyDecisions: ReplayPolicyDecision[] = [];
    const receivedSwingEvents: ConfirmedSwingEvent[] = [];
    const openedTrades: SimulatedTrade[] = [];
    const resolvedTrades: SimulatedTrade[] = [];
    try {
      do {
        const visibleStart = Math.max(0, Math.min(chartStartRef.current, target));
        const visibleEnd = Math.min(
          session.totalCandles - 1,
          visibleStart + Math.max(1, chartViewSizeRef.current) - 1,
        );
        response = await advanceReplayMutation.mutateAsync({
          sessionId: session.sessionId,
          data: {
            // Replay target is independent of the chart viewport; these fields
            // select only the cached CCT/CST states returned for display.
            toIndex: target,
            viewStart: visibleStart,
            viewEnd: Math.max(visibleStart, visibleEnd),
            simulationFilters: {
              direction: direction as 'ALL' | 'BUY' | 'SELL',
              strength: strength as 'ALL' | 'STRONG' | 'NEUTRAL' | 'WEAK',
              engulfingOnly,
            },
          },
        });
        if (
          generation !== replayGenerationRef.current
          || replaySessionRef.current?.sessionId !== session.sessionId
        ) return false;
        receivedStates.push(...response.newStates, ...response.visibleStates);
        receivedWickPressureStates.push(...response.newWickPressureStates, ...response.visibleWickPressureStates);
        receivedAlerts.push(...response.newAlerts);
        receivedPolicyDecisions.push(...response.policyDecisions);
        receivedSwingEvents.push(...(response.newSwingEvents ?? []));
        openedTrades.push(...response.newlyOpenedTrades);
        resolvedTrades.push(...response.newlyResolvedTrades);
        setSimulationSnapshot(response.simulation);
        setPolicySimulationSnapshot(response.policySimulation);
        setPolicySimulatedTrades(response.policyTrades);
        setComputedThrough(response.computedThrough);
      } while (response.hasMore);

      setStreamStates(previous => {
        const next = new Map(previous);
        for (const state of receivedStates) next.set(state.index, state);
        return next;
      });
      setStreamWickPressure(previous => {
        const next = new Map(previous);
        for (const state of receivedWickPressureStates) next.set(state.index, state.state);
        return next;
      });
      if (receivedAlerts.length) {
        setStreamAlerts(previous => [...previous, ...receivedAlerts]);
      }
      if (receivedPolicyDecisions.length) {
        setPolicyDecisions(previous => {
          const next = new Map(previous);
          for (const decision of receivedPolicyDecisions) {
            next.set(decision.alertId, decision);
          }
          return next;
        });
      }
      if (receivedSwingEvents.length) {
        setStreamSwingEvents(previous => [...previous, ...receivedSwingEvents]);
      }
      if (openedTrades.length || resolvedTrades.length) {
        setSimulatedTrades(previous => {
          const next = new Map(previous.map(trade => [trade.tradeId, trade]));
          for (const trade of openedTrades) next.set(trade.tradeId, trade);
          for (const trade of resolvedTrades) next.set(trade.tradeId, trade);
          return [...next.values()].sort((a, b) => a.entryIndex - b.entryIndex);
        });
      }
      setCurrentReplayCandle(response.currentCandle);
      cursorRef.current = target;
      setCursor(target);
      return true;
    } catch (error) {
      setPlaying(false);
      if (
        typeof error === 'object'
        && error !== null
        && 'status' in error
        && error.status === 404
      ) {
        // Replay sessions are held in API memory and disappear after restarts.
        // Clear a stranded client session so Retry can create a fresh one.
        clearReplay();
      }
      return false;
    } finally {
      advanceInFlightRef.current = false;
    }
  }, [advanceReplayMutation, direction, strength, engulfingOnly, clearReplay]);
  useEffect(() => {
    advanceToRef.current = advanceTo;
  }, [advanceTo]);

  const stepTo = async (index: number): Promise<boolean> => {
    if (!replayReady) return false;
    const next = Math.max(0, Math.min(totalCandles - 1, index));
    setPlaying(false);
    if (advanceInFlightRef.current) return false;
    if (next < computedThrough) {
      const restarted = await startReplay(false, true);
      if (!restarted) return false;
    }
    return advanceTo(next);
  };

  const stepReplayFromChart = (direction: -1 | 1) => {
    setPlaying(false);
    const next = cursor + direction;
    if (next < 0 || next >= totalCandles) return;
    void stepTo(next);
  };

  const startReplay = async (
    playAfterStart: boolean,
    forceNewSession = false,
  ): Promise<boolean> => {
    setPlaying(false);
    setSelected(null);
    setComment(null);
    if (!activeDataset?.ownerApproved || !validDates || startReplayMutation.isPending) return false;
    if (matchingSession && !forceNewSession) {
      cursorRef.current = 0;
      setCursor(0);
      setCurrentReplayCandle(matchingSession.currentCandle);
      setFollowLatest(true);
      chartStartRef.current = 0;
      setChartStart(0);
      setPlaying(playAfterStart && matchingSession.totalCandles > 1);
      return true;
    }

    if (forceNewSession) {
      replayGenerationRef.current += 1;
      replaySessionRef.current = null;
      setReplaySession(null);
    }
    const generation = replayGenerationRef.current;
    try {
      const started = await startReplayMutation.mutateAsync({
        data: { symbol: selectedSymbol, timeframe, from, to, simulationSettings },
      });
      if (generation !== replayGenerationRef.current) return false;
      replaySessionRef.current = started;
      cursorRef.current = 0;
      setComputedThrough(started.computedThrough);
      setReplaySession(started);
      setSimulationSnapshot(started.simulation);
      setSimulatedTrades([]);
      setPolicySimulationSnapshot(started.policySimulation);
      setPolicySimulatedTrades(started.policyTrades);
      setPolicyDecisions(new Map());
      setCurrentReplayCandle(started.currentCandle);
      setStreamStates(new Map(started.states.map(state => [state.index, state])));
      setStreamWickPressure(new Map(started.wickPressureStates.map(state => [state.index, state.state])));
      setStreamAlerts([]);
      setStreamSwingEvents(started.swingEvents ?? []);
      setCursor(0);
      setFollowLatest(true);
      chartStartRef.current = 0;
      setChartStart(0);
      setPlaying(playAfterStart && started.totalCandles > 1);
      return true;
    } catch {
      setPlaying(false);
      return false;
    }
  };

  useEffect(() => {
    if (!playing || !replayReady || scrubbing || advanceReplayMutation.isPending) return;
    if (cursor >= totalCandles - 1) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => {
      const next = nextReplayPosition(cursor, totalCandles, currentMonthChunks);
      if (next.month) setViewMonth(next.month);
      void advanceToRef.current(next.index);
    }, 800 / speed);
    return () => window.clearTimeout(timer);
  }, [
    playing,
    replayReady,
    scrubbing,
    advanceReplayMutation.isPending,
    cursor,
    totalCandles,
    speed,
    currentMonthChunks,
  ]);

  const cursorMonth = monthForIndex(currentMonthChunks, cursor);
  useEffect(() => {
    if (replayReady && cursorMonth && cursorMonth !== viewMonth && !focusedTrade) {
      setViewMonth(cursorMonth);
    }
  }, [replayReady, cursorMonth, viewMonth, focusedTrade]);

  useEffect(() => {
    if (selected && !revealedAlerts.some(alert => alert.alertId === selected.alertId)) {
      setSelected(null);
      setComment(null);
    }
  }, [selected, revealedAlerts]);

  const runReplay = () => {
    if (playing) {
      setPlaying(false);
      return;
    }
    void startReplay(false, Boolean(matchingSession));
  };
  const selectAlert = (alert: Alert) => {
    const index = streamAlerts.find(item => item.alert.alertId === alert.alertId)?.candleIndex ?? 0;
    setPlaying(false);
    void advanceTo(index).then(moved => {
      if (moved) {
        setSelected(alert);
        setComment(null);
      }
    });
  };
  const requestNote = () => {
    if (!selected) return;
    commentHook.mutate({ data: { alert: selected } }, { onSuccess: setComment });
  };
  const resetWindow = () => { void startReplay(false, true); };
  const selectMonth = (month: string) => {
    setViewMonth(month);
    const chunk = currentMonthChunks.find(item => item.month === month);
    if (!chunk) return;
    if (replayReady) {
      void stepTo(chunk.startIndex);
      return;
    }
    cursorRef.current = chunk.startIndex;
    setCursor(chunk.startIndex);
    setFollowLatest(true);
    setChartStart(null);
  };
  const focusTrade = (trade: SimulatedTrade) => {
    if (focusedTrade?.tradeId === trade.tradeId) {
      setFocusedTrade(null);
      return;
    }
    setPlaying(false);
    setFocusedTrade(trade);
    setRightPanel('journal');
    const month = trade.entryTime.slice(0, 7);
    const resolutionMonth = (trade.exitTime ?? trade.entryTime).slice(0, 7);
    setViewMonth(replayReady && resolutionMonth !== month ? resolutionMonth : month);
    const revealTo = trade.exitIndex ?? trade.entryIndex;
    if (replayReady) {
      void stepTo(revealTo).then(ok => {
        if (!ok) return;
        const size = Math.max(chartViewSizeRef.current, Math.min(1000, (trade.exitIndex ?? trade.entryIndex) - trade.entryIndex + 24));
        chartViewSizeRef.current = size;
        setChartViewSize(size);
        const nextStart = Math.max(0, trade.entryIndex - 12);
        chartStartRef.current = nextStart;
        setChartStart(nextStart);
        setFollowLatest(false);
      });
    } else {
      const chunk = currentMonthChunks.find(item => item.month === month);
      if (chunk) {
        setCursor(Math.max(chunk.startIndex, trade.entryIndex));
        setChartStart(Math.max(chunk.startIndex, trade.entryIndex - Math.floor(chartViewSizeRef.current * .35)));
      }
    }
  };
  const sourceName = activeDataset?.source.split('/').at(-1) ?? 'Loading local source…';
  const pendingReviewCount = instrumentReviews.filter(review => !review.ownerApproved).length;
  const atEnd = replayReady && cursor >= totalCandles - 1;
  const togglePlayback = () => {
    if (!replayReady) {
      void startReplay(true);
      return;
    }
    if (atEnd) {
      void startReplay(true, true);
      return;
    }
    setPlaying(value => !value);
  };
  const maxReplayCandles = 100000;
  const replayLimitExceeded = totalCandles > maxReplayCandles;
  const replayFailed = startReplayMutation.isError || advanceReplayMutation.isError;
  const retryReplay = () => {
    if (matchingSession) void advanceTo(Math.min(totalCandles - 1, cursor + 1));
    else void startReplay(false);
  };
  const firstChartCandle = chartCandles.find((candle): candle is Candle => Boolean(candle));
  const lastChartCandle = [...chartCandles].reverse().find((candle): candle is Candle => Boolean(candle));
  const chartHasCurrentCandle = cursor >= chartStartIndex && cursor <= chartEndIndex;
  const panChartTo = (start: number) => {
    setFollowLatest(false);
    const next = Math.max(0, Math.min(start, maxChartStart));
    chartStartRef.current = next;
    setChartStart(next);
  };
  const zoomChartAt = (anchorFraction: number, factor: number) => {
    const currentVisibleSize = replayReady
      ? chartViewSizeRef.current
      : Math.min(chartViewSizeRef.current, Math.max(1, displayEnd + 1));
    const anchor = chartStartRef.current + Math.max(0, Math.min(1, anchorFraction)) * currentVisibleSize;
    const nextSize = Math.max(10, Math.min(1000, Math.round(chartViewSizeRef.current * factor)));
    const nextVisibleSize = replayReady
      ? nextSize
      : Math.min(nextSize, Math.max(1, displayEnd + 1));
    const nextMaxStart = Math.max(0, displayEnd + 1 - nextVisibleSize);
    const nextStart = Math.max(0, Math.min(
      Math.round(anchor - Math.max(0, Math.min(1, anchorFraction)) * nextVisibleSize),
      nextMaxStart,
    ));
    setFollowLatest(false);
    chartViewSizeRef.current = nextSize;
    chartStartRef.current = nextStart;
    setChartViewSize(nextSize);
    setChartStart(nextStart);
  };
  const jumpToLatest = () => {
    setFollowLatest(true);
    chartStartRef.current = maxChartStart;
    setChartStart(maxChartStart);
  };

  return <main className="terminal" data-theme={theme}>
    <header className="topline">
      <InstrumentHeading symbol={selectedSymbol}/>
      <div className="source-meta" title={activeDataset?.source ?? ''}>
        <span>{sourceName}</span><span className="source-hash">{activeDataset ? `${boundsStart}—${boundsEnd}` : '—'}</span><span className="source-hash">{activeDataset?.sourceSha256.slice(0, 12) ?? '—'}</span>
        <button
          type="button"
          className="theme-toggle"
          onClick={() => setTheme(value => value === 'dark' ? 'light' : 'dark')}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          data-testid="button-theme-toggle"
        >
          {theme === 'dark' ? <Sun size={13}/> : <Moon size={13}/>}
          <span>{theme === 'dark' ? 'LIGHT' : 'DARK'}</span>
        </button>
      </div>
    </header>

    <section className="review-strip" aria-label="Instrument owner review status" data-pending={pendingReviewCount > 0 ? 'true' : 'false'} data-testid="status-instrument-reviews">
      <span className="review-strip-title">DATA REVIEW</span>
      <div className="review-strip-symbols">
        {instrumentReviews.length
          ? instrumentReviews.map(review => (
            <span
              key={review.symbol}
              className="review-chip"
              data-approved={String(review.ownerApproved)}
              title={`${review.status} · ${review.rowCount.toLocaleString()} rows`}
              data-testid={`review-${review.symbol.toLowerCase()}`}
              aria-label={`${review.symbol}: ${review.ownerApproved
                ? 'approved'
                : review.status.replace(/_/g, ' ').toLowerCase()}`}
            >
              <b>{review.symbol}</b>
              <i>{review.ownerApproved
                ? 'APPROVED'
                : review.status === 'PENDING_OWNER_SPOTCHECK'
                  ? 'PENDING SPOT-CHECK'
                  : review.status.replace(/_/g, ' ')}</i>
            </span>
          ))
          : <span className="review-strip-loading">Loading instrument status…</span>}
      </div>
      <span className="review-strip-note">
        {pendingReviewCount
          ? `${pendingReviewCount} PENDING · REPLAY SCORING DISABLED`
          : 'ALL SOURCES OWNER-APPROVED'}
      </span>
    </section>

    <section className="querybar" aria-label="Historical review settings">
      <label className="compact-field" title="Chart display starts on this date and continues through the latest available data.">
        <span>FROM · CHART START · UTC</span>
        <input type="date" aria-label="From date: chart display start" min={boundsStart} max={boundsEnd} value={from} onChange={e => { setFrom(e.target.value); setViewMonth(e.target.value.slice(0, 7)); clearReplay(); }} data-testid="input-from-date"/>
      </label>
      <label className="compact-field" title="Replay, generated alerts, and simulated trades stop on this date.">
        <span>TO · SIMULATION END · UTC</span>
        <input type="date" aria-label="To date: simulation end" min={boundsStart} max={boundsEnd} value={to} onChange={e => { const value = e.target.value; setTo(value); if (value && viewMonth > displayTo.slice(0, 7)) setViewMonth(displayTo.slice(0, 7)); clearReplay(); }} data-testid="input-to-date"/>
      </label>
      <SymbolSelect symbol={selectedSymbol} reviews={instrumentReviews} onChange={selectSymbol}/>
      {selectedSymbol === 'XAUUSD' && <>
        <label className="compact-field month-field"><span>MONTH</span><select value={viewMonth} onChange={e => selectMonth(e.target.value)} data-testid="select-month">
          {XAU_MONTHS.map(month => <option key={month} value={month} disabled={month < from.slice(0, 7) || month > displayTo.slice(0, 7)}>{month}</option>)}
        </select></label>
        <span className="month-status" role="status" data-testid="status-month-progress">{monthProgress} · {viewMonth}</span>
      </>}
      <div className="timeframes" role="group" aria-label="Timeframe">
        {(['M1', 'M5', 'M15', 'H1', 'H4', 'D1'] as Timeframe[]).map(tf => <button key={tf} className={timeframe === tf ? 'tf active' : 'tf'} onClick={() => {
          if (tf === timeframe) return;
          setTimeframe(tf); clearReplay();
        }} data-testid={`button-timeframe-${tf.toLowerCase()}`}>{tf}</button>)}
      </div>
      <button className="run-control" onClick={runReplay} disabled={!activeDataset?.ownerApproved || !validDates || dataset.isLoading || dataset.isError || startReplayMutation.isPending || (advanceReplayMutation.isPending && !playing) || candlesQuery.isLoading || replayLimitExceeded} aria-label={playing ? 'Stop simulation' : matchingSession ? 'Replay from start' : 'Run replay'} title={playing ? 'Stop simulation' : matchingSession ? 'Replay from start' : 'Run replay'} data-advance-busy={advanceReplayMutation.isPending ? 'true' : undefined} data-testid="button-run-smoke">
        {playing ? <Square size={13} fill="currentColor"/> : <Play size={13} fill="currentColor"/>}{playing ? 'STOP SIMULATION' : startReplayMutation.isPending ? 'STARTING…' : matchingSession ? 'REPLAY FROM START' : 'RUN / REPLAY'}
      </button>
      <span className="loaded-state" data-testid="status-dataset"><b data-testid="status-selected-symbol">{selectedSymbol}</b> · {dataset.isError ? 'SOURCE ERROR' : dataset.isLoading ? 'SOURCE…' : `${totalCandles.toLocaleString()} CANDLES ${timeframe === 'M1' ? 'AVAILABLE' : 'LOADED'}`}</span>
      {activeDataset && !activeDataset.ownerApproved && <div className="m1-notice" role="status" data-testid="notice-pending-owner-review">{selectedSymbol} is pending owner approval. Replay and scoring remain disabled until the manifest is approved.</div>}
      {timeframe === 'M1' && <div className="m1-notice" data-testid="notice-m1-review">M1 fires on ~1 in 4 candles — for engine checks only, not human review.</div>}
      {replayLimitExceeded && <div className="m1-notice" data-testid="notice-m1-engine-limit">Replay windows are capped at 100,000 candles. Choose a shorter date range.</div>}
      {loadStatus && <div className="load-status" role="status" aria-live="polite" data-testid="status-loading">{loadStatus}</div>}
      {!validDates && <div className="inline-error" role="alert">Choose a date window within the available source bounds.</div>}
      {dataset.isError && <button className="inline-error retry" onClick={() => dataset.refetch()} data-testid="button-retry-dataset">Dataset unavailable · Retry</button>}
      {candlesQuery.isError && <button className="inline-error retry" onClick={() => candlesQuery.refetch()} data-testid="button-retry-candles">Candle window unavailable · Retry</button>}
      {replayFailed && <button className="inline-error retry" onClick={retryReplay} data-testid="button-retry-smoke">Replay failed · Retry</button>}
    </section>

    <section className="replay-bar" aria-label="Replay controls">
      <div className="replay-buttons">
        <button className="transport" disabled={!replayReady} onClick={resetWindow} aria-label="Reset replay to first candle" title="Reset to first candle"><SkipBack size={15}/></button>
        <button className="transport" disabled={!replayReady || cursor <= 0 || advanceReplayMutation.isPending} data-advance-busy={advanceReplayMutation.isPending && replayReady && cursor > 0 ? 'true' : undefined} onClick={() => { void stepTo(cursor - 1); }} aria-label="Previous candle" data-testid="button-previous-candle"><ChevronLeft size={17}/></button>
        <button
          className="transport play-toggle"
          disabled={!activeDataset?.ownerApproved || !validDates || dataset.isLoading || dataset.isError || startReplayMutation.isPending || advanceReplayMutation.isPending || replayLimitExceeded || (candlesQuery.isLoading && !replayReady)}
          data-advance-busy={advanceReplayMutation.isPending && replayReady ? 'true' : undefined}
          onClick={togglePlayback}
          aria-label={playing ? 'Pause replay' : !replayReady ? 'Run replay' : atEnd ? 'Replay from start' : 'Play replay'}
          title={playing ? 'Pause replay' : !replayReady ? 'Run replay' : atEnd ? 'Replay from start' : 'Play replay'}
          data-testid="button-play-pause"
        >{playing ? <Pause size={15} fill="currentColor"/> : <Play size={15} fill="currentColor"/>}</button>
        <button className="transport" disabled={!replayReady || atEnd || advanceReplayMutation.isPending} data-advance-busy={advanceReplayMutation.isPending && replayReady && !atEnd ? 'true' : undefined} onClick={() => { void stepTo(cursor + 1); }} aria-label="Next candle" data-testid="button-next-candle"><ChevronRight size={17}/></button>
        <button className="transport" disabled={!replayReady} onClick={resetWindow} aria-label="Reset to first candle" title="Reset"><RotateCcw size={14}/></button>
      </div>
      <label className="seek-control"><span className="seek-label">SEEK</span><input type="range" min="0" max={Math.max(0, totalCandles - 1)} value={replayReady ? (scrubbing ? scrubPosition : cursor) : Math.max(0, totalCandles - 1)} disabled={!replayReady} aria-disabled={advanceReplayMutation.isPending} onPointerDown={e => { if (advanceInFlightRef.current) { e.preventDefault(); return; } beginScrub(); }} onPointerUp={endScrub} onPointerCancel={endScrub} onBlur={endScrub} onKeyDown={e => { if (advanceInFlightRef.current) { e.preventDefault(); return; } beginScrub(); }} onKeyUp={endScrub} onChange={e => { if (advanceInFlightRef.current) return; const next = Number(e.target.value); scrubPositionRef.current = next; setScrubPosition(next); }} data-testid="slider-replay-position"/></label>
      <div className="playback-rate" role="group" aria-label="Playback speed">
        {[0.5, 1, 2, 5].map(rate => <button key={rate} className={speed === rate ? 'rate selected' : 'rate'} disabled={!replayReady} onClick={() => setSpeed(rate)} data-testid={`button-speed-${String(rate).replace('.', '-')}`}>{rate}×</button>)}
      </div>
      <div className="time-readout"><span>{fmtTime(activeCandle?.time)}</span><b>{replayReady ? `${Math.min(cursor + 1, totalCandles).toLocaleString()} / ${totalCandles.toLocaleString()}` : `— / ${totalCandles.toLocaleString()}`}</b></div>
    </section>

    <div className="workbench">
      <section className="chart-column">
        <div className="chart-heading">
          <div className="chart-title" data-testid="chart-title">{timeframe}<span> · {selectedSymbol} · HISTORICAL</span></div>
          <div className="chart-ohlc" data-testid="candle-ohlc">
            {(['open', 'high', 'low', 'close'] as const).map(key => <span key={key}><i>{key[0].toUpperCase()}</i>{fmtPrice(activeCandle?.[key], selectedSymbol)}</span>)}
          </div>
        </div>
        <div className="chart-overlay-controls" role="group" aria-label="Chart overlays">
          <button type="button" className={`overlay-toggle${showCct ? ' active' : ''}`} aria-pressed={showCct} aria-label="CCT overlay" onClick={() => setShowCct(value => !value)} data-testid="toggle-overlay-cct">CCT</button>
          <button type="button" className={`overlay-toggle${showCst ? ' active' : ''}`} aria-pressed={showCst} aria-label="CST overlay" onClick={() => setShowCst(value => !value)} data-testid="toggle-overlay-cst">CST</button>
          <button type="button" className={`overlay-toggle${showWickPressure ? ' active' : ''}`} aria-pressed={showWickPressure} aria-label="Wick Pressure overlay" onClick={() => setShowWickPressure(value => !value)} data-testid="toggle-overlay-wick-pressure">WP</button>
          <button type="button" className={`overlay-toggle${showDc ? ' active' : ''}`} aria-pressed={showDc} aria-label="Directional Change shadow overlay" onClick={() => setShowDc(value => !value)} data-testid="toggle-overlay-dc">DC</button>
          <button type="button" className={`overlay-toggle${showHier ? ' active' : ''}`} aria-pressed={showHier} aria-label="Hierarchical Swings shadow overlay" onClick={() => setShowHier(value => !value)} data-testid="toggle-overlay-hier">HIER</button>
          <button type="button" className={`overlay-toggle${showAlertMarkers ? ' active' : ''}`} aria-pressed={showAlertMarkers} aria-label="Alert markers" onClick={() => setShowAlertMarkers(value => !value)} data-testid="toggle-overlay-alerts">ALERTS</button>
          <button type="button" className={`overlay-toggle${showStrengthLabels ? ' active' : ''}`} aria-pressed={showStrengthLabels} aria-label="S/N/W strength labels" onClick={() => setShowStrengthLabels(value => !value)} data-testid="toggle-overlay-strength">S/N/W</button>
          <button type="button" className={`overlay-toggle${showCrosshair ? ' active' : ''}`} aria-pressed={showCrosshair} aria-label="Crosshair" title="Toggle chart crosshair" onClick={() => setShowCrosshair(value => !value)} data-testid="toggle-crosshair">CROSSHAIR</button>
          <button type="button" className={`overlay-toggle${showTradeLevels ? ' active' : ''}`} aria-pressed={showTradeLevels} aria-label="Trade levels overlay" onClick={() => setShowTradeLevels(value => !value)} data-testid="toggle-trade-levels">TRADE LEVELS</button>
          <DrawingToolbar
            tool={drawingTool}
            onTool={setDrawingTool}
            onClear={requestClearSelectedDrawings}
            showPrices={showAnnotationPrices}
            onTogglePrices={() => setShowAnnotationPrices(value => !value)}
          />
          <div className="chart-nav-controls" role="group" aria-label="Chart navigation">
            <button type="button" className="overlay-toggle" onClick={() => zoomChartAt(0.5, 1.3)} aria-label="Zoom out" title="Zoom out" data-testid="button-chart-zoom-out">−</button>
            <button type="button" className="overlay-toggle" onClick={() => zoomChartAt(0.5, 0.75)} aria-label="Zoom in" title="Zoom in" data-testid="button-chart-zoom-in">+</button>
            <button
              type="button"
              className={`overlay-toggle${followLatest ? ' active' : ''}`}
              onClick={() => followLatest ? setFollowLatest(false) : jumpToLatest()}
              aria-pressed={followLatest}
              aria-label={followLatest ? 'Disable auto-scroll to latest candle' : 'Scroll to latest candle and enable auto-scroll'}
              title={followLatest ? 'Auto-scroll is on' : 'Scroll to latest and follow playback'}
              disabled={!totalCandles}
              data-testid="button-chart-latest"
            >AUTO SCROLL</button>
          </div>
        </div>
        <div className="swing-controls" role="group" aria-label="Swing labels">
          <button
            type="button"
            className={`overlay-toggle${showSwings ? ' active' : ''}`}
            aria-pressed={showSwings}
            aria-label="Show swing labels"
            onClick={() => setShowSwings(value => !value)}
            data-testid="toggle-overlay-swings"
          >SWINGS</button>
          <div className="swing-family-controls" aria-label="Swing families">
            {SWING_FAMILY_CHOICES.map(({ family, label }) => (
              <label key={family}>
                <input
                  type="checkbox"
                  checked={swingFamilies[family]}
                  aria-label={`${label} swing labels`}
                  data-testid={`toggle-swing-family-${family.toLowerCase()}`}
                  onChange={event => {
                    const checked = event.currentTarget.checked;
                    setSwingFamilies(current => ({
                      ...current,
                      [family]: checked,
                    }));
                  }}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </div>
        <div className="shadow-disclaimer" role="note" data-testid="shadow-overlay-disclaimer">
          DC and HIER are read-only shadows; they never affect alerts, strength, or simulation.
        </div>
        {chartLoadingMessage || ((candlesQuery.isLoading && !candleData && !chartCandles.some(Boolean)) || (m1DataReplay && !activeCandle && !chartCandles.some(Boolean))) ? <div className="chart-loading" role="status" aria-live="polite" data-testid="status-chart-loading">
          <div className="chart-loading-card">
            <span className="chart-loading-spinner" aria-hidden="true"/>
            <strong>{chartLoadingMessage ?? `Preparing ${selectedSymbol} chart…`}</strong>
            <span>You can switch symbols while this loads.</span>
          </div>
        </div> :
          <Chart
            key={`${selectedSymbol}:${timeframe}`}
            symbol={selectedSymbol}
            timeframe={timeframe}
            candles={chartCandles}
            alerts={chartAlerts}
            active={chartHasCurrentCandle ? activeCandle : null}
            onAlert={selectAlert}
            showCct={showCct}
            showCst={showCst}
            showWickPressure={showWickPressure}
            showDc={showDc}
            showHier={showHier}
            showAlertMarkers={showAlertMarkers}
            showStrengthLabels={showStrengthLabels}
            showCrosshair={showCrosshair}
            showAnnotationPrices={showAnnotationPrices}
            swingEvents={matchingSession ? streamSwingEvents : candleData?.swingEvents ?? []}
            showSwings={showSwings}
            swingFamilies={swingFamilies}
            trades={simulationSnapshot?.enabled ? visibleSimulatedTrades : []}
            showTradeLevels={showTradeLevels}
            focusedTrade={focusedTrade}
            drawingTool={drawingTool}
            onDrawingTool={setDrawingTool}
            replayIndex={matchingSession ? cursor : displayEnd}
            replayReady={replayReady}
            onReplayStep={stepReplayFromChart}
            viewStart={chartStartIndex}
            viewSize={visibleViewSize}
            onPanTo={panChartTo}
            onZoomAt={zoomChartAt}
            onInteraction={() => setFollowLatest(false)}
          />}
        <div className="chart-foot">
          <span data-testid="trend-overlay-state">{matchingSession
            ? `ENGINE REPLAY · CCT ${activeCandle?.cct ?? '—'} · CST ${activeCandle?.cst ?? '—'}${showWickPressure ? ` · WP ${wickPressureLabel(activeCandle?.wickPressure)}` : ''}`
            : `APPROVED HISTORICAL CANDLES · UTC${showWickPressure ? ' · WP —' : ''}`}</span>
          <span>{firstChartCandle && lastChartCandle ? `${fmtTime(firstChartCandle.time)} — ${fmtTime(lastChartCandle.time)}` : 'NO DATA'}</span>
        </div>
        {candleData?.truncated && <div className="truncation">Window truncated · {candleData.candles.length.toLocaleString()} of {candleData.totalCandles.toLocaleString()} candles.</div>}
        {matchingSession && <div className="run-stamp" data-testid="status-run">INCREMENTAL SESSION · {Math.max(0, computedThrough + 1).toLocaleString()} / {totalCandles.toLocaleString()} COMPUTED · {matchingSession.runId}</div>}
      </section>

      <div className="right-panel">
        <div className="right-panel-tabs" role="tablist" aria-label="Right-side workstation panels">
          <button role="tab" aria-selected={rightPanel === 'alerts'} className={rightPanel === 'alerts' ? 'active' : ''} onClick={() => setRightPanel('alerts')} data-testid="tab-alert-tape">ALERT TAPE</button>
          <button role="tab" aria-selected={rightPanel === 'journal'} className={rightPanel === 'journal' ? 'active' : ''} onClick={() => setRightPanel('journal')} data-testid="tab-trade-journal">TRADE JOURNAL</button>
        </div>
      {rightPanel === 'journal' ? <TradeJournal
        trades={displayedSimulatedTrades}
        symbol={selectedSymbol}
        selectedTradeId={focusedTrade?.tradeId ?? null}
        onSelect={focusTrade}
      /> : <aside className="alert-pane">
        <div className="alert-header">
          <div><strong>ALERT TAPE</strong><span>{matchingSession ? 'INCREMENTAL ENGINE OUTPUT · REVEALED' : m1DataReplay ? 'CANDLE-ONLY M1 REPLAY · NO ENGINE OUTPUT' : 'PRESS RUN TO BEGIN REPLAY'}</span></div>
          <b data-testid="count-revealed-alerts">{revealedAlerts.length.toString().padStart(2, '0')}</b>
        </div>
        <div className="countline" aria-label="Revealed alert counts">
          <span>BUY <b>{revealedCounts.buy}</b></span><span>SELL <b>{revealedCounts.sell}</b></span>
          <span>STR <b>{revealedCounts.strong}</b></span><span>NEU <b>{revealedCounts.neutral}</b></span><span>WEAK <b>{revealedCounts.weak}</b></span>
        </div>
        <div className="filters">
          <select value={direction} onChange={e => setDirection(e.target.value)} aria-label="Direction filter" data-testid="filter-alert-direction"><option value="ALL">ALL DIR</option><option value="BUY">BUY</option><option value="SELL">SELL</option></select>
          <select value={strength} onChange={e => setStrength(e.target.value)} aria-label="Strength filter" data-testid="filter-alert-strength"><option value="ALL">ALL STRENGTH</option><option value="STRONG">STRONG</option><option value="NEUTRAL">NEUTRAL</option><option value="WEAK">WEAK</option></select>
          <select value={variantFilter} onChange={e => setVariantFilter(e.target.value as 'ALL' | 'A' | 'B')} aria-label="Variant filter" data-testid="filter-alert-variant"><option value="ALL">ALL VAR</option><option value="A">A</option><option value="B">B</option></select>
          <select value={wickPressureFilter} onChange={e => setWickPressureFilter(e.target.value as 'ALL' | WickPressure)} aria-label="Wick pressure filter" data-testid="filter-alert-wick-pressure"><option value="ALL">ALL WP</option><option value="up-pressure">UP</option><option value="balanced">BALANCED</option><option value="down-pressure">DOWN</option></select>
          <select className="filter-engulfing" value={engulfingOnly ? 'ENGULF' : 'ALL'} onChange={e => setEngulfingOnly(e.target.value === 'ENGULF')} aria-label="Engulfing filter" data-testid="filter-alert-engulfing"><option value="ALL">ALL ENGULF</option><option value="ENGULF">ENGULF</option></select>
          <button
            type="button"
            className={`filter-mtf${mtfAgreementOnly ? ' active' : ''}`}
            aria-pressed={mtfAgreementOnly}
            aria-label="Show only MTF trend-agreeing alerts"
            title={timeframe === 'M5' ? 'Filter to alerts agreeing with the last closed M15 CST' : 'MTF filter is available on M5'}
            disabled={timeframe !== 'M5'}
            onClick={() => setMtfAgreementOnly(value => !value)}
            data-testid="filter-alert-mtf"
          >{mtfAgreementOnly ? 'MTF ONLY' : 'MTF ALL'}</button>
          <AiFilterToggle
            enabled={aiFilterEnabled}
            onToggle={() => setAiFilterEnabled(value => !value)}
          />
        </div>
        <AiFilterHiddenSummary
          hidden={hiddenByAiFilter}
          formatTime={fmtTime}
        />
        <div className="alert-filter-stats" data-testid="alert-filter-stats" aria-live="polite">
          <span>{shownAlertItems.length} ALERTS</span>
          <span>{filteredAlertStats.closedTradeCount} CLOSED</span>
          <span>WR {filteredAlertStats.winRatePct === null ? '—' : `${filteredAlertStats.winRatePct.toFixed(1)}%`}</span>
          <span>AVG R {filteredAlertStats.averageR === null ? '—' : `${filteredAlertStats.averageR >= 0 ? '+' : ''}${filteredAlertStats.averageR.toFixed(2)}`}</span>
          <span>EXP {filteredAlertStats.expectancyAfterCosts === null ? '—' : `${filteredAlertStats.expectancyAfterCosts >= 0 ? '+' : ''}${filteredAlertStats.expectancyAfterCosts.toFixed(2)}R`}</span>
          <span>P&L {filteredAlertStats.totalPnlAfterCosts >= 0 ? `+$${filteredAlertStats.totalPnlAfterCosts.toFixed(2)}` : `-$${Math.abs(filteredAlertStats.totalPnlAfterCosts).toFixed(2)}`}</span>
        </div>
        <div className="alert-list" data-testid="alert-list">
          <div className="alert-sort-head" aria-label="Sortable alert feature columns">
            <span />
            <span className="alert-column-label">ALERT</span>
            {sortButton('wp', 'WP')}
            {sortButton('engulf', 'ENG')}
            {sortButton('variant', 'VAR')}
            <span />
          </div>
          {!matchingSession ? <div className="tape-empty">{m1DataReplay ? <>M1 candle replay is loaded.<br/>No engine run was started.</> : <>No engine output yet.<br/>Press RUN, then play — alerts fire as candles arrive.</>}</div> :
            shownAlertItems.length === 0 ? <div className="tape-empty">{revealedAlerts.length ? 'No revealed alerts match filters.' : 'No alerts revealed at this candle.'}</div> :
              shownAlertItems.map(({ alert, wpState }) => <div key={alert.alertId} className={`alert-row ${selected?.alertId === alert.alertId ? 'chosen' : ''}`} role="button" tabIndex={0} onClick={() => selectAlert(alert)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') selectAlert(alert); }} data-testid={`alert-row-${alert.alertId}`}>
                <span className={`direction-mark ${alert.direction.toLowerCase()}`}>{alert.direction === 'BUY' ? <ArrowUpRight size={15}/> : <ArrowDownRight size={15}/>}</span>
                <span className="alert-main">
                  <b>{alert.direction}<small>{alert.strengthLabel}</small></b>
                  <i>{fmtTime(alert.time)}</i><em>{alert.symbol} · {alert.timeframe}</em>
                  <span
                    className={`alert-mtf-badge${alert.mtfTrendAgree ? ' agrees' : ''}`}
                    title={`M15 CST ${alert.mtfCst ?? '—'} · CCT ${alert.mtfCct ?? '—'} (${alert.mtfCctAgree ? 'agree' : 'no match'}) · WP ${alert.mtfWickPressure ?? '—'} (${alert.mtfWpAgree ? 'agree' : 'no match'})${alert.dualTfAlert ? ' · same-direction M15 alert within the causal 6-candle window' : ''}`}
                    data-testid={`alert-mtf-${alert.alertId}`}
                  >{alert.mtfTrendAgree ? 'MTF ✓' : 'MTF —'}{alert.dualTfAlert ? ' · DUAL' : ''}</span>
                </span>
                <span className={`alert-feature-cell wp-${wpState ?? 'unknown'}`} title={`Wick pressure: ${wickPressureLabel(wpState)}`} data-testid={`alert-wp-${alert.alertId}`}>
                  {wpState === 'up-pressure' ? 'UP' : wpState === 'down-pressure' ? 'DOWN' : wpState === 'balanced' ? 'BAL' : '—'}
                </span>
                <span className={`alert-feature-cell ${alert.engulfing ? 'engulfing' : ''}`} title="Engulfing" data-testid={`alert-engulf-${alert.alertId}`}>{alert.engulfing ? 'Y' : '—'}</span>
                <span className={`alert-feature-cell variant-${alert.alertType?.endsWith('-B') ? 'b' : 'a'}`} title="Alert variant" data-testid={`alert-variant-${alert.alertId}`}>{alert.alertType?.endsWith('-B') ? 'B' : 'A'}</span>
                <span className="alert-action-slot">
                  {selected?.alertId === alert.alertId && <span className="note-action" onClick={e => e.stopPropagation()}>
                    <button onClick={requestNote} disabled={commentHook.isPending} aria-label="Request AI note" data-testid="button-generate-ai-comment">{commentHook.isPending ? '…' : 'AI NOTE'}</button>
                  </span>}
                </span>
              </div>)}
        </div>
        {selected && comment && comment.alertId === selected.alertId && <div className="comment-result" data-testid="result-ai-comment">{comment.available ? comment.comment : comment.message ?? 'Commentary unavailable.'}</div>}
        {selected && commentHook.isError && <div className="comment-error" role="alert">Note unavailable. Try again.</div>}
        {matchingSession && <div className="engine-meta">v1.5.0 · informational only · unscored</div>}
      </aside>}
      </div>
    </div>
    <SimulationPanel
      symbol={selectedSymbol}
      simulationCost={activeDataset?.simulationCost ?? 0}
      settings={simulationSettings}
      snapshot={simulationSnapshot}
      trades={visibleSimulatedTrades}
      onSettingsChange={next => {
        setLotSizesBySymbol(current => ({ ...current, [selectedSymbol]: next.lotSize }));
        setSimulationSettings(next);
        clearReplay();
      }}
      sessionActive={Boolean(matchingSession)}
    />
    <EvolveControl symbol={selectedSymbol} symbolApproved={Boolean(activeDataset?.ownerApproved)} />
    <footer className="disclaimer" data-testid="persistent-disclaimer">{DISCLAIMER}</footer>
  </main>;
}

function Router() {
  return <RouteBoundary><Switch><Route path="/" component={Home}/><Route component={NotFound}/></Switch></RouteBoundary>;
}
function RouteBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}
function App() {
  return <QueryClientProvider client={queryClient}><TooltipProvider><WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}><Router/></WouterRouter><Toaster/></TooltipProvider></QueryClientProvider>;
}
export default App;
