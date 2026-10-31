import { useEffect, useRef, useState, type MouseEvent, type PointerEvent as ReactPointerEvent } from 'react';
import {
  anchorToChartCoordinate,
  normalizeRectangle,
  type AnnotationDragMode,
  type ChartAnnotation,
  type AnnotationDraft,
  type PlotGeometry,
} from '@/lib/chart-annotations';
import type { Candle, InstrumentSymbol } from '@workspace/api-client-react';

export type DrawingTool = 'cursor' | 'line' | 'rectangle';

export function requestClearSelectedDrawings() {
  const detail = { clearedCount: 0 };
  window.dispatchEvent(new CustomEvent('forex-clear-selected-drawings', { detail }));
  return detail.clearedCount;
}

function DrawingToolIcon({ tool }: { tool: DrawingTool }) {
  const shared = {
    className: 'drawing-tool-icon',
    viewBox: '0 0 16 16',
    'aria-hidden': true,
    focusable: false,
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.4,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };

  if (tool === 'cursor') return <svg {...shared}>
    <path d="M8 1.5v3.25M8 11.25v3.25M1.5 8h3.25M11.25 8h3.25"/>
    <circle cx="8" cy="8" r="2.25"/>
  </svg>;
  if (tool === 'line') return <svg {...shared}>
    <path d="m3 13 10-10"/>
    <circle cx="3" cy="13" r="1"/>
    <circle cx="13" cy="3" r="1"/>
  </svg>;
  return <svg {...shared}>
    <rect x="3" y="3" width="10" height="10" rx=".5"/>
  </svg>;
}

export function DrawingToolbar({ tool, onTool, onClear, showPrices, onTogglePrices }: {
  tool: DrawingTool | null;
  onTool: (tool: DrawingTool | null) => void;
  onClear: () => number;
  showPrices: boolean;
  onTogglePrices: () => void;
}) {
  const [clearFeedback, setClearFeedback] = useState('');
  const [clearFlashing, setClearFlashing] = useState(false);
  const feedbackTimer = useRef<number | null>(null);
  const flashTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current);
    if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
  }, []);

  const clearSelected = () => {
    const clearedCount = onClear();
    setClearFeedback(clearedCount
      ? `Cleared ${clearedCount} object${clearedCount === 1 ? '' : 's'}`
      : 'Select an object first');
    setClearFlashing(true);
    if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current);
    if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => {
      setClearFlashing(false);
      flashTimer.current = null;
    }, 200);
    feedbackTimer.current = window.setTimeout(() => {
      setClearFeedback('');
      feedbackTimer.current = null;
    }, 1600);
  };

  return <div className="drawing-tools" role="toolbar" aria-label="Chart drawing tools">
    {([
      ['cursor', 'CRSR'],
      ['line', 'LINE'],
      ['rectangle', 'RECT'],
    ] as const).map(([value, label]) => <button
      key={value}
      type="button"
      className={`overlay-toggle drawing-tool-toggle${tool === value ? ' active' : ''}`}
      aria-pressed={tool === value}
      aria-label={`${label} drawing tool`}
      onClick={() => onTool(tool === value ? null : value)}
      data-testid={`annotation-tool-${value}`}
    ><DrawingToolIcon tool={value}/><span className="drawing-tool-label">{label}</span></button>)}
    <button
      type="button"
      className={`overlay-toggle drawing-price-toggle${showPrices ? ' active' : ''}`}
      aria-pressed={showPrices}
      aria-label={showPrices ? 'Hide annotation price labels' : 'Show annotation price labels'}
      title="Show or hide market prices attached to drawings"
      onClick={onTogglePrices}
      data-testid="toggle-annotation-prices"
    >PRICE</button>
    <button
      type="button"
      className={`overlay-toggle drawing-clear-button${clearFlashing ? ' is-flashing' : ''}`}
      aria-label="Clear selected drawings"
      onClick={clearSelected}
      data-testid="annotation-clear-selected"
    >CLEAR</button>
    {clearFeedback && <span
      className="drawing-clear-feedback"
      role="status"
      aria-live="polite"
      data-testid="drawing-clear-feedback"
    >{clearFeedback}</span>}
  </div>;
}

export function DrawingLayer({ annotations, draft, selectedId, candles, geometry, symbol, formatTime, formatPrice, showPrices, onSelect, onDelete, onDragStart }: {
  annotations: ChartAnnotation[];
  draft: AnnotationDraft | null;
  selectedId: string | null;
  candles: Array<Candle | undefined>;
  geometry: PlotGeometry;
  symbol: InstrumentSymbol;
  formatTime: (time: string) => string;
  formatPrice: (value: number | undefined, symbol: InstrumentSymbol) => string;
  showPrices: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onDragStart: (event: ReactPointerEvent<SVGElement>, annotation: ChartAnnotation, mode: AnnotationDragMode) => void;
}) {
  const renderAnnotation = (annotation: ChartAnnotation, isDraft = false) => {
    const start = anchorToChartCoordinate(annotation.start, candles, geometry);
    const end = anchorToChartCoordinate(annotation.end, candles, geometry);
    if (!start || !end) return null;
    const selected = selectedId === annotation.id && !isDraft;
    const contextDelete = (event: MouseEvent<SVGGElement>) => {
      event.preventDefault();
      event.stopPropagation();
      onDelete(annotation.id);
    };
    if (annotation.kind === 'line') return <g
      key={annotation.id}
      className={`chart-annotation line-annotation${selected ? ' selected' : ''}${isDraft ? ' annotation-draft' : ''}`}
      data-annotation-id={annotation.id}
      data-annotation-kind="line"
      data-testid={isDraft ? 'annotation-draft' : undefined}
      onPointerDown={event => !isDraft && onDragStart(event, annotation, 'move')}
      onContextMenu={contextDelete}
      onClick={() => !isDraft && onSelect(annotation.id)}
    >
      <title>{showPrices
        ? `Line · ${formatTime(annotation.start.time)} ${formatPrice(annotation.start.price, symbol)} to ${formatTime(annotation.end.time)} ${formatPrice(annotation.end.price, symbol)}`
        : `Line · ${formatTime(annotation.start.time)} to ${formatTime(annotation.end.time)}`}</title>
      <line
        className="annotation-hit-line"
        x1={start.x} y1={start.y} x2={end.x} y2={end.y}
        data-testid={`annotation-hit-area-${annotation.id}`}
      />
      <line className="annotation-visible-line" x1={start.x} y1={start.y} x2={end.x} y2={end.y}/>
      {showPrices && <g className="annotation-hover-label">
        <text x={start.x + 7} y={start.y - 8}>{formatPrice(annotation.start.price, symbol)}</text>
        <text x={end.x + 7} y={end.y - 8}>{formatPrice(annotation.end.price, symbol)}</text>
      </g>}
      {selected && <>
        <circle className="annotation-handle" cx={start.x} cy={start.y} r="5" aria-label="Adjust line start" onPointerDown={event => onDragStart(event, annotation, 'start')}/>
        <circle className="annotation-handle" cx={end.x} cy={end.y} r="5" aria-label="Adjust line end" onPointerDown={event => onDragStart(event, annotation, 'end')}/>
      </>}
    </g>;
    const left = Math.min(start.x, end.x);
    const right = Math.max(start.x, end.x);
    const top = Math.min(start.y, end.y);
    const bottom = Math.max(start.y, end.y);
    const handles: Array<{ mode: AnnotationDragMode; x: number; y: number }> = [
      { mode: 'nw', x: left, y: top }, { mode: 'n', x: (left + right) / 2, y: top },
      { mode: 'ne', x: right, y: top }, { mode: 'e', x: right, y: (top + bottom) / 2 },
      { mode: 'se', x: right, y: bottom }, { mode: 's', x: (left + right) / 2, y: bottom },
      { mode: 'sw', x: left, y: bottom }, { mode: 'w', x: left, y: (top + bottom) / 2 },
    ];
    return <g
      key={annotation.id}
      className={`chart-annotation rectangle-annotation${selected ? ' selected' : ''}${isDraft ? ' annotation-draft' : ''}`}
      data-annotation-id={annotation.id}
      data-annotation-kind="rectangle"
      data-testid={isDraft ? 'annotation-draft' : undefined}
      onPointerDown={event => !isDraft && onDragStart(event, annotation, 'move')}
      onContextMenu={contextDelete}
      onClick={() => !isDraft && onSelect(annotation.id)}
    >
      <title>{showPrices
        ? `Rectangle · ${formatPrice(annotation.start.price, symbol)} to ${formatPrice(annotation.end.price, symbol)} · ${formatTime(annotation.start.time)} to ${formatTime(annotation.end.time)}`
        : `Rectangle · ${formatTime(annotation.start.time)} to ${formatTime(annotation.end.time)}`}</title>
      <rect
        className="annotation-rectangle-hit-area"
        x={left}
        y={top}
        width={Math.max(1, right - left)}
        height={Math.max(1, bottom - top)}
        data-testid={`annotation-hit-area-${annotation.id}`}
      />
      <rect className="annotation-rectangle-body" x={left} y={top} width={Math.max(1, right - left)} height={Math.max(1, bottom - top)}/>
      {showPrices && <g className="annotation-hover-label">
        <text x={right + 7} y={top + 10}>{formatPrice(annotation.start.price, symbol)}</text>
        <text x={right + 7} y={bottom}>{formatPrice(annotation.end.price, symbol)}</text>
      </g>}
      {selected && handles.map(handle => <circle
        key={handle.mode}
        className={`annotation-handle handle-${handle.mode}`}
        cx={handle.x} cy={handle.y} r="4"
        aria-label={`Resize rectangle ${handle.mode}`}
        onPointerDown={event => onDragStart(event, annotation, handle.mode)}
      />)}
    </g>;
  };

  return <g className="chart-annotations" data-testid="chart-annotations">
    {annotations.map(annotation => renderAnnotation(annotation))}
    {draft && renderAnnotation({
      id: 'annotation-draft-preview',
      kind: draft.kind,
      start: draft.kind === 'rectangle' ? normalizeRectangle(draft.start, draft.end).start : draft.start,
      end: draft.kind === 'rectangle' ? normalizeRectangle(draft.start, draft.end).end : draft.end,
    }, true)}
  </g>;
}
