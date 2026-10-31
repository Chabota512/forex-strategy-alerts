import { readFileSync } from 'node:fs';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { Candle } from '@workspace/api-client-react';
import { Chart } from '../App';
import { annotationStorageKey, type AnnotationTool, type ChartAnnotation } from '@/lib/chart-annotations';
import { DrawingToolbar, requestClearSelectedDrawings } from './drawing-tools';

const stylesheet = readFileSync('src/index.css', 'utf8');

function ruleBody(selector: string) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = stylesheet.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  if (!match) throw new Error(`Missing CSS rule for ${selector}`);
  return match[1];
}

function variableValue(selector: string, variable: string) {
  const value = ruleBody(selector).match(new RegExp(`${variable}:\\s*([^;]+);`))?.[1]?.trim();
  if (!value) throw new Error(`Missing ${variable} in ${selector}`);
  return value;
}

const chartCandles = [
  { time: '2023-01-01T00:00:00Z', open: 100, high: 100.2, low: 99.8, close: 100, volume: 1 },
  { time: '2023-01-01T00:15:00Z', open: 100, high: 100.2, low: 99.8, close: 100, volume: 1 },
  { time: '2023-01-01T00:30:00Z', open: 100, high: 100.2, low: 99.8, close: 100, volume: 1 },
  { time: '2023-01-01T00:45:00Z', open: 100, high: 100.2, low: 99.8, close: 100, volume: 1 },
] as unknown as Candle[];
const storageKey = annotationStorageKey('XAUUSD', 'M15');

const lineDrawing: ChartAnnotation = {
  id: 'line-body-drag',
  kind: 'line',
  start: { time: chartCandles[1].time, price: 100.1 },
  end: { time: chartCandles[2].time, price: 99.9 },
};
const rectangleDrawing: ChartAnnotation = {
  id: 'rectangle-body-drag',
  kind: 'rectangle',
  start: { time: chartCandles[1].time, price: 100.2 },
  end: { time: chartCandles[2].time, price: 99.8 },
};
const firstRectangle: ChartAnnotation = {
  id: 'rectangle-clear-one',
  kind: 'rectangle',
  start: { time: chartCandles[0].time, price: 100.2 },
  end: { time: chartCandles[1].time, price: 99.9 },
};
const secondRectangle: ChartAnnotation = {
  id: 'rectangle-clear-two',
  kind: 'rectangle',
  start: { time: chartCandles[2].time, price: 100.15 },
  end: { time: chartCandles[3].time, price: 99.85 },
};

function chartProps(
  annotations: ChartAnnotation[],
  onPanTo = vi.fn(),
  onZoomAt = vi.fn(),
) {
  window.localStorage.setItem(storageKey, JSON.stringify(annotations));
  return {
    symbol: 'XAUUSD' as const,
    timeframe: 'M15' as const,
    candles: chartCandles,
    alerts: [],
    active: null,
    onAlert: vi.fn(),
    showCct: false,
    showCst: false,
    showWickPressure: false,
    showDc: false,
    showHier: false,
    showAlertMarkers: false,
    showStrengthLabels: false,
    swingEvents: [],
    showSwings: false,
    swingFamilies: { MAJOR: true, MINOR: true, DC: true, HIER: true },
    trades: [],
    showTradeLevels: false,
    focusedTrade: null,
    showCrosshair: true,
    showAnnotationPrices: true,
    drawingTool: 'cursor' as const,
    onDrawingTool: vi.fn(),
    replayIndex: chartCandles.length - 1,
    replayReady: false,
    onReplayStep: vi.fn(),
    viewStart: 0,
    viewSize: chartCandles.length,
    onPanTo,
    onZoomAt,
    onInteraction: vi.fn(),
  };
}

function ChartHarness({ props, initialTool = 'cursor' }: {
  props: ReturnType<typeof chartProps>;
  initialTool?: AnnotationTool | null;
}) {
  const [tool, setTool] = useState<AnnotationTool | null>(initialTool);
  const [showPrices, setShowPrices] = useState(true);
  return <>
    <DrawingToolbar
      tool={tool}
      onTool={setTool}
      onClear={requestClearSelectedDrawings}
      showPrices={showPrices}
      onTogglePrices={() => setShowPrices(value => !value)}
    />
    <Chart {...props} drawingTool={tool} onDrawingTool={setTool} showAnnotationPrices={showPrices}/>
  </>;
}

function mockChartViewport(chart: SVGSVGElement) {
  vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    width: 1100,
    height: 480,
    top: 0,
    right: 1100,
    bottom: 480,
    left: 0,
    toJSON: () => ({}),
  });
}

function pointerAt(clientX: number, clientY: number) {
  return { clientX, clientY, pointerId: 7, pointerType: 'mouse', button: 0 };
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe('drawing toolbar', () => {
  it.each([
    ['cursor', 'CRSR'],
    ['line', 'LINE'],
    ['rectangle', 'RECT'],
  ] as const)('shows an icon and label for the %s tool', (tool, label) => {
    render(
      <DrawingToolbar
        tool={tool}
        onTool={vi.fn()}
        onClear={() => 0}
        showPrices
        onTogglePrices={vi.fn()}
      />,
    );

    const button = screen.getByTestId(`annotation-tool-${tool}`);
    const icon = button.querySelector('svg');
    expect(icon).not.toBeNull();
    expect(icon?.getAttribute('aria-hidden')).toBe('true');
    expect(button.textContent).toContain(label);
    expect(button.getAttribute('aria-pressed')).toBe('true');
  });

  it('disarms an active tool when clicked again', () => {
    const view = render(<ChartHarness props={chartProps([])}/>);
    const line = screen.getByTestId('annotation-tool-line');

    fireEvent.click(line);
    expect(line.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(line);

    expect(line.getAttribute('aria-pressed')).toBe('false');
    for (const tool of ['cursor', 'line', 'rectangle']) {
      const button = screen.getByTestId(`annotation-tool-${tool}`);
      expect(button.getAttribute('aria-pressed')).toBe('false');
      expect(button.className).not.toContain('active');
    }
    const chart = view.container.querySelector('.price-chart');
    expect(chart?.classList.contains('cursor-mode')).toBe(true);
    expect(chart?.classList.contains('drawing-mode')).toBe(false);
  });

  it('toggles only annotation price labels without changing the armed drawing tool', () => {
    render(<ChartHarness props={chartProps([])}/>);
    const line = screen.getByTestId('annotation-tool-line');
    const prices = screen.getByTestId('toggle-annotation-prices');

    fireEvent.click(line);
    fireEvent.click(prices);

    expect(line.getAttribute('aria-pressed')).toBe('true');
    expect(prices.getAttribute('aria-pressed')).toBe('false');
    expect(prices.textContent).toBe('PRICE');
  });

  it('can turn off CRSR without arming another tool', () => {
    render(<ChartHarness props={chartProps([])}/>);
    fireEvent.click(screen.getByTestId('annotation-tool-cursor'));

    for (const tool of ['cursor', 'line', 'rectangle']) {
      const button = screen.getByTestId(`annotation-tool-${tool}`);
      expect(button.getAttribute('aria-pressed')).toBe('false');
      expect(button.className).not.toContain('active');
    }
  });

  it('switches directly between drawing tools', () => {
    render(<ChartHarness props={chartProps([])}/>);
    const line = screen.getByTestId('annotation-tool-line');
    const rectangle = screen.getByTestId('annotation-tool-rectangle');

    fireEvent.click(line);
    fireEvent.click(rectangle);

    expect(line.getAttribute('aria-pressed')).toBe('false');
    expect(line.className).not.toContain('active');
    expect(rectangle.getAttribute('aria-pressed')).toBe('true');
    expect(rectangle.className).toContain('active');
  });

  it('uses plain cursor behavior with crosshair and keyboard stepping when all tools are off', () => {
    const onReplayStep = vi.fn();
    const props = {
      ...chartProps([]),
      replayReady: true,
      onReplayStep,
    };
    const view = render(<ChartHarness props={props}/>);
    const line = screen.getByTestId('annotation-tool-line');
    fireEvent.click(line);
    fireEvent.click(line);

    const chart = view.container.querySelector('.price-chart') as unknown as SVGSVGElement;
    mockChartViewport(chart);
    expect(chart.classList.contains('cursor-mode')).toBe(true);
    expect(chart.classList.contains('drawing-mode')).toBe(false);

    fireEvent.click(chart);
    expect(document.activeElement).toBe(chart);
    fireEvent.pointerMove(chart, pointerAt(300, 200));
    expect(chart.querySelector('.chart-crosshair')).not.toBeNull();
    expect(chart.querySelector('[data-testid="crosshair-time-label"]')?.textContent).toContain('2023');

    fireEvent.keyDown(chart, { key: 'ArrowRight' });
    expect(onReplayStep).toHaveBeenCalledWith(1);
  });

  it('does not step the replay while a drawing gesture is active and Escape blurs', () => {
    const onReplayStep = vi.fn();
    const props = { ...chartProps([]), replayReady: true, onReplayStep };
    const view = render(<ChartHarness props={props}/>);
    const chart = view.container.querySelector('.price-chart') as unknown as SVGSVGElement;
    mockChartViewport(chart);

    fireEvent.click(screen.getByTestId('annotation-tool-line'));
    fireEvent.pointerDown(chart, pointerAt(300, 200));
    fireEvent.keyDown(chart, { key: 'ArrowRight' });
    expect(onReplayStep).not.toHaveBeenCalled();

    fireEvent.keyDown(chart, { key: 'Escape' });
    expect(document.activeElement).not.toBe(chart);
  });

  it.each([true, false])('shows or hides annotation price labels when enabled=%s', showPrices => {
    const props = { ...chartProps([lineDrawing, rectangleDrawing]), showAnnotationPrices: showPrices };
    const view = render(<Chart {...props}/>);
    const labels = view.container.querySelectorAll('.annotation-hover-label text');
    const titles = [...view.container.querySelectorAll('.chart-annotation title')]
      .map(title => title.textContent ?? '');

    expect(labels).toHaveLength(showPrices ? 4 : 0);
    expect(titles.some(title => title.includes('100.1'))).toBe(showPrices);
  });

  it.each(['dark', 'light'] as const)(
    'keeps crosshair label foreground distinct from its chip in %s mode',
    theme => {
      const themeSelector = theme === 'dark'
        ? '.terminal'
        : ".terminal[data-theme='light']";
      const foreground = variableValue(themeSelector, '--text-heading');
      const background = variableValue(themeSelector, '--surface-raised');

      expect(ruleBody('.crosshair-axis-label')).toMatch(/fill:\s*var\(--text-heading\)/);
      expect(ruleBody('.crosshair-axis-tag')).toMatch(/fill:\s*var\(--surface-raised\)/);

      const fixture = document.createElement('div');
      fixture.className = 'terminal';
      fixture.dataset.theme = theme;
      const chip = document.createElement('div');
      const label = document.createElement('span');
      chip.style.backgroundColor = background;
      label.style.color = foreground;
      fixture.append(chip, label);
      document.body.append(fixture);

      expect(getComputedStyle(label).color)
        .not.toBe(getComputedStyle(chip).backgroundColor);
      fixture.remove();
    },
  );

  it('prevents chart text selection and click-focus outlines', () => {
    expect(ruleBody('.chart-frame')).toMatch(/user-select:\s*none/);
    expect(stylesheet).toMatch(
      /\.price-chart:focus\s*,\s*\.price-chart:focus-visible\s*\{\s*outline:\s*none;/,
    );
  });

  it('clears only the selected drawing and reports the count', () => {
    const view = render(<ChartHarness props={chartProps([])}/>);
    const chart = view.container.querySelector('.price-chart') as unknown as SVGSVGElement;
    mockChartViewport(chart);
    Object.defineProperty(chart, 'setPointerCapture', {
      configurable: true,
      value: vi.fn(),
    });

    fireEvent.click(screen.getByTestId('annotation-tool-rectangle'));
    const drawRectangle = (x1: number, y1: number, x2: number, y2: number) => {
      fireEvent.pointerDown(chart, pointerAt(x1, y1));
      fireEvent.pointerMove(chart, pointerAt(x2, y2));
      fireEvent.pointerUp(chart, pointerAt(x2, y2));
    };
    drawRectangle(100, 100, 350, 220);
    drawRectangle(550, 140, 800, 300);
    fireEvent.click(screen.getByTestId('annotation-tool-cursor'));

    const rectangles = chart.querySelectorAll('[data-annotation-kind="rectangle"]');
    expect(rectangles).toHaveLength(2);
    const selected = rectangles[0] as SVGGElement;
    const remainingId = rectangles[1].getAttribute('data-annotation-id');

    fireEvent.click(selected);
    fireEvent.click(screen.getByTestId('annotation-clear-selected'));

    expect(chart.querySelector(`[data-annotation-id="${selected.getAttribute('data-annotation-id')}"]`)).toBeNull();
    expect(chart.querySelector(`[data-annotation-id="${remainingId}"]`)).not.toBeNull();
    expect(screen.getByTestId('drawing-clear-feedback').textContent).toBe('Cleared 1 object');
    expect(screen.getByTestId('annotation-clear-selected').className).toContain('is-flashing');
  });

  it('keeps every drawing and reports when nothing is selected', () => {
    const props = chartProps([firstRectangle, secondRectangle]);
    const view = render(<>
      <DrawingToolbar
        tool="cursor"
        onTool={vi.fn()}
        onClear={requestClearSelectedDrawings}
        showPrices
        onTogglePrices={vi.fn()}
      />
      <Chart {...props}/>
    </>);
    const chart = view.container.querySelector('.price-chart') as unknown as SVGSVGElement;
    const clearButton = screen.getByTestId('annotation-clear-selected');

    expect(clearButton.hasAttribute('disabled')).toBe(false);
    fireEvent.click(clearButton);

    expect(chart.querySelectorAll('[data-annotation-id]')).toHaveLength(2);
    expect(screen.getByTestId('drawing-clear-feedback').textContent).toBe('Select an object first');
    expect(clearButton.className).toContain('is-flashing');
  });

  it.each([
    ['line', lineDrawing, 4],
    ['rectangle', rectangleDrawing, -4],
  ] as const)('moves a %s from its body without panning the chart', (kind, drawing, hitOffset) => {
    const onPanTo = vi.fn();
    const onZoomAt = vi.fn();
    const props = chartProps([drawing], onPanTo, onZoomAt);
    const view = render(<Chart {...props}/>);
    const chart = view.container.querySelector('.price-chart') as unknown as SVGSVGElement;
    mockChartViewport(chart);
    const group = chart.querySelector(`[data-annotation-id="${drawing.id}"]`) as SVGGElement;
    const hitArea = group.querySelector(`[data-testid="annotation-hit-area-${drawing.id}"]`)!;
    const objectCapture = vi.fn();
    const chartCapture = vi.fn();
    Object.defineProperty(group, 'setPointerCapture', {
      configurable: true,
      value: objectCapture,
    });
    Object.defineProperty(chart, 'setPointerCapture', {
      configurable: true,
      value: chartCapture,
    });

    let startX: number;
    let startY: number;
    if (kind === 'line') {
      const line = hitArea as SVGLineElement;
      startX = (Number(line.getAttribute('x1')) + Number(line.getAttribute('x2'))) / 2;
      startY = (Number(line.getAttribute('y1')) + Number(line.getAttribute('y2'))) / 2 + hitOffset;
    } else {
      const rectangle = hitArea as SVGRectElement;
      startX = Number(rectangle.getAttribute('x')) + Number(rectangle.getAttribute('width')) / 2;
      startY = Number(rectangle.getAttribute('y')) + hitOffset;
    }

    fireEvent.pointerDown(hitArea, pointerAt(startX, startY));
    fireEvent.pointerMove(group, pointerAt(startX + 100, startY - 12));
    const inProgress = JSON.parse(window.localStorage.getItem(storageKey) ?? '[]') as ChartAnnotation[];
    expect(inProgress[0].start.price).toBeGreaterThan(drawing.start.price);
    fireEvent.wheel(chart, { deltaY: -100, clientX: startX + 249 });
    fireEvent.pointerUp(group, pointerAt(startX + 249, startY - 30));

    const moved = JSON.parse(window.localStorage.getItem(storageKey) ?? '[]') as ChartAnnotation[];
    expect(objectCapture).toHaveBeenCalledWith(7);
    expect(chartCapture).not.toHaveBeenCalled();
    expect(moved[0].start.time).toBe(chartCandles[2].time);
    expect(moved[0].end.time).toBe(chartCandles[3].time);
    expect(moved[0].start.price).toBeGreaterThan(drawing.start.price);
    expect(moved[0].start.price - drawing.start.price)
      .toBeCloseTo(moved[0].end.price - drawing.end.price);
    expect(onPanTo).not.toHaveBeenCalled();
    expect(onZoomAt).not.toHaveBeenCalled();
  });

  it('gives line and rectangle bodies a 10px non-scaling drag target', () => {
    expect(ruleBody('.chart-annotation .annotation-hit-line'))
      .toMatch(/stroke-width:\s*10/);
    expect(ruleBody('.chart-annotation .annotation-hit-line'))
      .toMatch(/pointer-events:\s*stroke/);
    expect(ruleBody('.chart-annotation .annotation-rectangle-hit-area'))
      .toMatch(/stroke-width:\s*10/);
    expect(ruleBody('.chart-annotation .annotation-rectangle-hit-area'))
      .toMatch(/vector-effect:\s*non-scaling-stroke/);
    expect(ruleBody('.drawing-clear-button.is-flashing'))
      .toMatch(/200ms/);
  });
});
