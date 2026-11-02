import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { InstrumentReviewInfo, InstrumentSymbol, SimulatedTrade } from '@workspace/api-client-react';
import { Home } from './App';
import { InstrumentHeading, instrumentLabel, SymbolSelect } from '@/components/instrument-controls';
import { TooltipProvider } from '@/components/ui/tooltip';

const api = vi.hoisted(() => ({
  dataset: vi.fn(),
  candles: vi.fn(),
  start: vi.fn(),
  advance: vi.fn(),
  comment: vi.fn(),
  policy: vi.fn(),
  setMinSiScore: vi.fn(),
}));

vi.mock('@workspace/api-client-react', () => ({
  getGetLabCandlesQueryKey: (params: unknown) => ['lab-candles', params],
  useGetLabDataset: (...args: unknown[]) => api.dataset(...args),
  useGetLabCandles: (...args: unknown[]) => api.candles(...args),
  useGetEvolvePolicy: (...args: unknown[]) => api.policy(...args),
  useSetEvolvePolicyMinSiScore: () => ({
    mutate: api.setMinSiScore,
    isPending: false,
    isError: false,
  }),
  useStartLabReplaySession: () => ({
    mutateAsync: api.start,
    isPending: false,
    isError: false,
  }),
  useAdvanceLabReplaySession: () => ({
    mutateAsync: api.advance,
    isPending: false,
    isError: false,
  }),
  useGetTradeComment: () => ({
    mutate: api.comment,
    isPending: false,
    isError: false,
  }),
  useGetAlertComment: () => ({
    mutate: api.comment,
    isPending: false,
    isError: false,
  }),
}));

vi.mock('@/components/evolve-control', () => ({
  default: ({ symbol }: { symbol: string }) => <output data-testid="evolve-scope">{symbol}</output>,
}));

vi.mock('@/components/autoevolve-control', () => ({
  default: () => null,
}));

vi.mock('@/components/system-intelligence', () => ({
  default: () => null,
}));

const symbols: InstrumentSymbol[] = ['XAUUSD', 'GBPUSD', 'USDJPY', 'GBPJPY', 'AUDJPY'];
const approvedReviews = symbols.map(symbol => ({
  symbol,
  status: 'OWNER_APPROVED',
  ownerApproved: true,
  rowCount: 10,
  sourceSha256: 'a'.repeat(64),
}) as unknown as InstrumentReviewInfo);

function datasetFor(symbol: InstrumentSymbol) {
  return {
    symbol,
    source: `/lab/data/${symbol}_M1.csv`,
    sourceSha256: 'a'.repeat(64),
    startDate: symbol === 'AUDJPY'
      ? '2024-01-01'
      : symbol === 'XAUUSD' ? '2023-01-01' : '2025-01-01',
    endDate: '2025-12-31',
    candleCount: 10,
    reviewStatus: 'OWNER_APPROVED',
    ownerApproved: true,
    scoringEnabled: true,
    simulationCost: symbol === 'XAUUSD' ? 0.35 : 0.0001,
    instrumentReviews: approvedReviews,
    availableTimeframes: ['M1', 'M5', 'M15', 'H1', 'H4', 'D1'],
    defaultReviewTimeframes: ['M5', 'M15'],
    m1ReviewNotice: '',
    ownerAttestedSanityMetrics: {},
  };
}

function candle(time: string, close: number) {
  return {
    time,
    open: close - 0.01,
    high: close + 0.03,
    low: close - 0.03,
    close,
    volume: 1,
  };
}

function alertPayload() {
  return {
    candleIndex: 1,
    alert: {
      alertId: 'gbp-alert-1',
      time: '2025-01-01T00:05:00Z',
      symbol: 'GBPUSD',
      timeframe: 'M15',
      direction: 'BUY',
      alertType: 'BUY-A',
      engulfing: false,
      cct: 'ranging',
      cst: 'uptrend',
      dcState: 'uptrend',
      hierState: 'uptrend',
      strengthLabel: 'STRONG',
      note: null,
      candle: candle('2025-01-01T00:05:00Z', 1.26),
      previousCandle: candle('2025-01-01T00:00:00Z', 1.25),
      scored: false,
    },
  };
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  const datasets = new Map<InstrumentSymbol, ReturnType<typeof datasetFor>>();
  const candleResponses = new Map<string, ReturnType<typeof api.candles>>();
  api.policy.mockReturnValue({
    data: { mode: 'passthrough' },
    isLoading: false,
    isError: false,
  });
  api.dataset.mockImplementation((params: { symbol: InstrumentSymbol }) => ({
    data: datasets.get(params.symbol) ?? (() => {
      const data = datasetFor(params.symbol);
      datasets.set(params.symbol, data);
      return data;
    })(),
    isLoading: false,
    isFetching: false,
    isError: false,
    refetch: vi.fn(),
  }));
  api.candles.mockImplementation((params: {
    symbol: InstrumentSymbol;
    timeframe: string;
    from: string;
    to: string;
    chunkMonth: string;
  }) => {
    const cacheKey = JSON.stringify([
      params.symbol,
      params.timeframe,
      params.from,
      params.to,
      params.chunkMonth,
      (params as { chunkOffset?: number }).chunkOffset ?? 0,
    ]);
    const cached = candleResponses.get(cacheKey);
    if (cached) return cached;
    const base = params.symbol === 'GBPUSD' ? 1.25 : 2000;
    const month = params.chunkMonth;
    const goldChunks = [
      { month: '2023-01', startIndex: 0, count: 1 },
      { month: '2023-02', startIndex: 1, count: 1 },
    ];
    const chunkOffset = params.symbol === 'XAUUSD' && month === '2023-02' ? 1 : 0;
    const response = {
      data: {
        symbol: params.symbol,
        timeframe: params.timeframe,
        from: params.from,
        to: params.to,
        candles: params.symbol === 'GBPUSD'
          ? [
              {
                time: '2025-01-01T00:00:00Z',
                open: base,
                high: base + 0.001,
                low: base - 0.001,
                close: base + 0.0002,
                volume: 1,
              },
              {
                time: '2025-01-01T00:05:00Z',
                open: base + 0.0002,
                high: base + 0.0012,
                low: base - 0.0008,
                close: base + 0.0004,
                volume: 1,
              },
            ]
          : params.symbol === 'XAUUSD'
            ? [
                candle(
                  month === '2023-02' ? '2023-02-01T00:00:00Z' : '2023-01-02T00:00:00Z',
                  base + chunkOffset * 0.01,
                ),
              ]
            : [
                candle('2025-01-01T00:00:00Z', base),
                candle('2025-01-01T00:05:00Z', base + 0.01),
              ],
        totalCandles: 2,
        limit: 100000,
        truncated: false,
        chunks: params.symbol === 'XAUUSD'
          ? goldChunks
          : [{ month: '2025-01', startIndex: 0, count: 2 }],
        chunkMonth: month,
        chunkOffset,
        source: 'dukascopy',
        sourceSha256: 'a'.repeat(64),
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    };
    candleResponses.set(cacheKey, response);
    return response;
  });
  api.start.mockImplementation(async (request: {
    data: {
      symbol: InstrumentSymbol;
      timeframe: string;
      from: string;
      to: string;
      swingDirectionVersion: 'V1' | 'V2';
    };
  }) => {
    const { symbol, timeframe, from, to, swingDirectionVersion } = request.data;
    const slug = symbol === 'GBPUSD' ? 'gbp' : symbol.toLowerCase();
    return {
      sessionId: `session-${slug}`,
      symbol,
      runId: `run-${slug}`,
      timeframe,
      from,
      to,
      swingDirectionVersion,
      totalCandles: 2,
      cursor: 0,
      computedThrough: 0,
      states: [],
      wickPressureStates: [],
      simulation: null,
      currentCandle: candle(
        '2025-01-01T00:00:00Z',
        symbol === 'GBPUSD' ? 1.25 : 2000,
      ),
    };
  });
  api.advance.mockResolvedValue({
    newStates: [],
    visibleStates: [],
    newWickPressureStates: [],
    visibleWickPressureStates: [],
    newAlerts: [alertPayload()],
    policyDecisions: [],
    newlyOpenedTrades: [],
    newlyResolvedTrades: [],
    simulation: null,
    computedThrough: 1,
    hasMore: false,
    currentCandle: candle('2025-01-01T00:05:00Z', 1.26),
  });
});

afterEach(cleanup);

describe('instrument controls', () => {
  it('lists all registered symbols and disables pending manifests', () => {
    const reviews = approvedReviews.map(review => ({
      ...review,
      ownerApproved: review.symbol !== 'GBPJPY' && review.symbol !== 'AUDJPY',
      status: review.symbol === 'GBPJPY' || review.symbol === 'AUDJPY'
        ? 'PENDING_OWNER_SPOTCHECK'
        : 'OWNER_APPROVED',
    }));

    render(
      <SymbolSelect
        symbol="XAUUSD"
        reviews={reviews}
        onChange={vi.fn()}
      />,
    );

    const select = screen.getByTestId('select-symbol') as HTMLSelectElement;
    expect(Array.from(select.options).map(option => option.value)).toEqual(symbols);
    expect(select.querySelector('option[value="GBPUSD"]')?.hasAttribute('disabled')).toBe(false);
    expect(select.querySelector('option[value="GBPJPY"]')?.hasAttribute('disabled')).toBe(true);
    expect(select.querySelector('option[value="AUDJPY"]')?.textContent).toContain('pending');
  });

  it('updates the header pair label and falls back to unknown symbols', () => {
    const { rerender } = render(<InstrumentHeading symbol="GBPUSD" />);
    expect(screen.getByTestId('selected-pair-label').textContent)
      .toBe('GBPUSD · BRITISH POUND / U.S. DOLLAR');

    rerender(<InstrumentHeading symbol="TESTPAIR" />);
    expect(screen.getByTestId('selected-pair-label').textContent).toBe('TESTPAIR');
    expect(instrumentLabel(undefined)).toBeNull();
  });
});

describe('symbol switching session', () => {
  it('filters the alert tape only after enabling the active AI policy', async () => {
    api.policy.mockReturnValue({
      data: { mode: 'active' },
      isLoading: false,
      isError: false,
    });
    api.start.mockResolvedValueOnce({
      sessionId: 'session-ai-filter',
      symbol: 'XAUUSD',
      runId: 'run-ai-filter',
      timeframe: 'M15',
      swingDirectionVersion: 'V2',
      from: '2023-01-01',
      to: '2023-01-31',
      totalCandles: 2,
      cursor: 0,
      computedThrough: 0,
      states: [],
      wickPressureStates: [],
      simulation: null,
      currentCandle: candle('2023-01-02T00:00:00Z', 2000),
    });
    const hiddenAlert = alertPayload();
    hiddenAlert.alert.alertId = 'hidden-alert';
    const visibleAlert = alertPayload();
    visibleAlert.alert.alertId = 'visible-alert';
    api.advance.mockResolvedValueOnce({
      newStates: [],
      visibleStates: [],
      newWickPressureStates: [],
      visibleWickPressureStates: [],
      newAlerts: [hiddenAlert, visibleAlert],
      policyDecisions: [
        {
          alertId: 'hidden-alert',
          passed: false,
          reasons: ['min_strength=STRONG: alert strength is WEAK'],
        },
        { alertId: 'visible-alert', passed: true, reasons: [] },
      ],
      newlyOpenedTrades: [],
      newlyResolvedTrades: [],
      simulation: null,
      policySimulation: null,
      policyTrades: [],
      computedThrough: 1,
      hasMore: false,
      currentCandle: candle('2025-01-01T00:05:00Z', 2000.01),
    });

    render(<TooltipProvider><Home /></TooltipProvider>);

    const toggle = await screen.findByTestId('filter-alert-ai');
    expect(toggle.textContent).toBe('SYSTEM INTELLIGENCE OFF');
    const runButton = screen.getByTestId('button-run-smoke') as HTMLButtonElement;
    expect(runButton.disabled).toBe(false);
    fireEvent.click(runButton);
    expect(api.start).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId('status-run').textContent).toContain('INCREMENTAL SESSION'));
    fireEvent.click(screen.getByTestId('button-next-candle'));

    expect(await screen.findByTestId('alert-row-hidden-alert')).toBeTruthy();
    expect(screen.getByTestId('alert-row-visible-alert')).toBeTruthy();
    fireEvent.click(toggle);

    expect(screen.queryByTestId('alert-row-hidden-alert')).toBeNull();
    expect(screen.getByTestId('alert-row-visible-alert')).toBeTruthy();
    expect(screen.getByTestId('ai-filter-hidden-summary').textContent).toBe(
      '1 hidden by System Intelligence',
    );
    fireEvent.click(screen.getByTestId('ai-filter-hidden-summary'));
    expect(screen.getByTestId('ai-filter-hidden').textContent).toContain(
      'min_strength=STRONG: alert strength is WEAK',
    );
  });

  it('opens gold in January 2023 with five selectable timeframes', () => {
    render(<TooltipProvider><Home /></TooltipProvider>);

    expect((screen.getByTestId('input-from-date') as HTMLInputElement).value)
      .toBe('2023-01-01');
    expect((screen.getByTestId('input-to-date') as HTMLInputElement).value)
      .toBe('2023-01-31');
    expect((screen.getByTestId('select-month') as HTMLSelectElement).value).toBe('2023-01');
    expect(screen.getByTestId('status-month-progress').textContent)
      .toContain('Month 1/36');
    expect((screen.getByTestId('status-selected-symbol').textContent)).toBe('XAUUSD');
    expect(screen.getByTestId('button-timeframe-m15').className).toContain('active');
    expect(api.candles.mock.calls.some(([params]) => params.timeframe === 'M15')).toBe(true);
    expect(screen.queryByTestId('button-timeframe-m1')).toBeNull();
    for (const timeframe of ['m5', 'm15', 'h1', 'h4', 'd1']) {
      expect(screen.getByTestId(`button-timeframe-${timeframe}`)).toBeTruthy();
    }
  });

  it('hides V1 layers in Gold V2 and restores their saved toggle states in V1', async () => {
    render(<TooltipProvider><Home /></TooltipProvider>);

    fireEvent.click(await screen.findByTestId('button-swing-version-v1'));
    const cct = await screen.findByTestId('toggle-overlay-cct');
    const cst = screen.getByTestId('toggle-overlay-cst');
    const dc = screen.getByTestId('toggle-overlay-dc');
    const hier = screen.getByTestId('toggle-overlay-hier');
    const major = screen.getByTestId('toggle-swing-family-major') as HTMLInputElement;
    const minor = screen.getByTestId('toggle-swing-family-minor') as HTMLInputElement;
    const swingDc = screen.getByTestId('toggle-swing-family-dc') as HTMLInputElement;
    const swingHier = screen.getByTestId('toggle-swing-family-hier') as HTMLInputElement;

    fireEvent.click(cct);
    fireEvent.click(dc);
    fireEvent.click(major);
    fireEvent.click(swingDc);
    fireEvent.click(screen.getByTestId('button-swing-version-v2'));

    expect(screen.getByTestId('v1-layers-hidden').textContent?.trim())
      .toBe('V1 layers hidden — V2 active');
    for (const testId of [
      'toggle-overlay-cct',
      'toggle-overlay-cst',
      'toggle-overlay-dc',
      'toggle-overlay-hier',
      'toggle-swing-family-major',
      'toggle-swing-family-minor',
      'toggle-swing-family-dc',
      'toggle-swing-family-hier',
    ]) {
      expect(screen.queryByTestId(testId)).toBeNull();
    }
    for (const testId of [
      'button-swing-version-v1',
      'button-swing-version-v2',
      'toggle-overlay-alerts',
      'toggle-overlay-strength',
      'toggle-crosshair',
      'annotation-tool-cursor',
      'toggle-overlay-wick-pressure',
      'toggle-trade-levels',
      'toggle-overlay-swings',
    ]) {
      expect(screen.getByTestId(testId)).toBeTruthy();
    }

    fireEvent.click(screen.getByTestId('button-swing-version-v1'));

    expect(screen.queryByTestId('v1-layers-hidden')).toBeNull();
    expect(screen.getByTestId('toggle-overlay-cct').getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByTestId('toggle-overlay-cst').getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('toggle-overlay-dc').getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByTestId('toggle-overlay-hier').getAttribute('aria-pressed')).toBe('true');
    expect((screen.getByTestId('toggle-swing-family-major') as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId('toggle-swing-family-minor') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId('toggle-swing-family-dc') as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId('toggle-swing-family-hier') as HTMLInputElement).checked).toBe(true);

    fireEvent.change(screen.getByTestId('select-symbol'), {
      target: { value: 'GBPUSD' },
    });
    await waitFor(() => {
      expect(screen.getByTestId('status-selected-symbol').textContent).toBe('GBPUSD');
    });
    expect(screen.getByTestId('button-swing-version-v2').hasAttribute('disabled')).toBe(true);
    expect(screen.getByTestId('toggle-overlay-cct')).toBeTruthy();
  });

  it('defaults AUDJPY to January 2024 after a symbol switch', async () => {
    render(<TooltipProvider><Home /></TooltipProvider>);

    fireEvent.change(await screen.findByTestId('select-symbol'), {
      target: { value: 'AUDJPY' },
    });
    await waitFor(() => {
      expect(screen.getByTestId('status-selected-symbol').textContent).toBe('AUDJPY');
      expect((screen.getByTestId('input-from-date') as HTMLInputElement).value)
        .toBe('2024-01-01');
      expect((screen.getByTestId('input-to-date') as HTMLInputElement).value)
        .toBe('2024-01-31');
    });
  });

  it('shows crosshair labels and keeps a rectangle after zooming out', async () => {
    render(<TooltipProvider><Home /></TooltipProvider>);
    const chart = await screen.findByRole('img', {
      name: 'Historical XAUUSD candlestick replay chart',
    }) as unknown as SVGSVGElement;
    const chartFrame = chart.closest('.chart-frame');
    fireEvent.click(chart);
    expect(document.activeElement).toBe(chart);
    expect(chartFrame?.classList.contains('selected')).toBe(false);
    const [, , chartWidth, chartHeight] = (chart.getAttribute('viewBox') ?? '')
      .split(/\s+/)
      .map(Number);
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      width: chartWidth,
      height: chartHeight,
      top: 0,
      right: chartWidth,
      bottom: chartHeight,
      left: 0,
      toJSON: () => ({}),
    });
    Object.defineProperty(chart, 'setPointerCapture', {
      configurable: true,
      value: vi.fn(),
    });
    const pointerAt = (x: number, y: number) => ({
      clientX: Math.round(chartWidth * x),
      clientY: Math.round(chartHeight * y),
      pointerId: 1,
      pointerType: 'mouse',
      button: 0,
    });

    fireEvent.pointerMove(chart, pointerAt(0.45, 0.5));
    const timeLabel = screen.getByTestId('crosshair-time-label').textContent ?? '';
    expect(timeLabel).toContain('2023');
    expect(timeLabel).toMatch(/\d{2}:\d{2}/);
    expect(screen.getByTestId('crosshair-price-label').textContent).not.toBe('');
    const crosshairLines = Array.from(
      chart.querySelectorAll('.chart-crosshair .crosshair-line'),
    );
    expect(crosshairLines).toHaveLength(2);
    for (const line of crosshairLines) {
      expect(line.getAttribute('stroke-dasharray')).toBe('3 4');
      expect(line.getAttribute('opacity')).toBe('0.55');
    }
    fireEvent.click(screen.getByTestId('toggle-crosshair'));
    expect(chart.querySelector('.chart-crosshair')).toBeNull();
    fireEvent.click(screen.getByTestId('toggle-crosshair'));
    expect(chart.querySelector('.chart-crosshair')).not.toBeNull();

    fireEvent.click(screen.getByTestId('annotation-tool-rectangle'));
    fireEvent.pointerDown(chart, pointerAt(0.42, 0.42));
    fireEvent.pointerMove(chart, pointerAt(0.58, 0.62));
    fireEvent.pointerUp(chart, pointerAt(0.58, 0.62));
    const annotation = chart.querySelector('[data-annotation-kind="rectangle"]');
    expect(annotation).not.toBeNull();
    fireEvent.click(annotation!);
    expect(annotation?.classList.contains('selected')).toBe(true);
    expect(annotation?.querySelectorAll('.annotation-handle').length).toBe(8);
    expect(chartFrame?.classList.contains('selected')).toBe(false);
    const title = annotation?.querySelector('title')?.textContent;
    expect(title).toContain('Rectangle ·');

    fireEvent.click(screen.getByTestId('button-chart-zoom-out'));
    fireEvent.click(screen.getByTestId('button-chart-zoom-out'));
    const zoomedAnnotation = chart.querySelector('[data-annotation-kind="rectangle"]');
    expect(zoomedAnnotation?.querySelector('title')?.textContent).toBe(title);
  });

  it('steps the focused chart with arrows, pauses playback, and blurs on Escape', async () => {
    let sessionNumber = 0;
    api.start.mockImplementation(async () => {
      sessionNumber += 1;
      return {
        sessionId: `session-keyboard-${sessionNumber}`,
        symbol: 'XAUUSD',
        runId: `run-keyboard-${sessionNumber}`,
        timeframe: 'M15',
        swingDirectionVersion: 'V2',
        from: '2023-01-01',
        to: '2023-01-31',
        totalCandles: 3,
        cursor: 0,
        computedThrough: 0,
        states: [],
        wickPressureStates: [],
        simulation: null,
        currentCandle: candle('2023-01-02T00:00:00Z', 2000),
      };
    });
    api.advance.mockImplementation(async (request: {
      data: { toIndex: number };
    }) => {
      const index = request.data.toIndex;
      return {
        newStates: [],
        visibleStates: [],
        newWickPressureStates: [],
        visibleWickPressureStates: [],
        newAlerts: [],
        policyDecisions: [],
        newlyOpenedTrades: [],
        newlyResolvedTrades: [],
        simulation: null,
        computedThrough: index,
        hasMore: false,
        currentCandle: index === 0
          ? candle('2023-01-02T00:00:00Z', 2000)
          : candle('2023-01-02T00:15:00Z', 2000.02),
      };
    });

    render(<TooltipProvider><Home /></TooltipProvider>);
    const chart = await screen.findByRole('img', {
      name: 'Historical XAUUSD candlestick replay chart',
    }) as unknown as SVGSVGElement;
    fireEvent.click(screen.getByTestId('button-play-pause'));
    await waitFor(() => {
      expect(screen.getByTestId('status-run').textContent).toContain('INCREMENTAL SESSION');
      expect(screen.getByTestId('button-play-pause').getAttribute('aria-label'))
        .toBe('Pause replay');
    });

    fireEvent.click(chart);
    expect(document.activeElement).toBe(chart);
    await waitFor(() => expect(chart.querySelector('.chart-crosshair')).not.toBeNull());
    const initialCrosshairX = chart.querySelector('.chart-crosshair .crosshair-line')
      ?.getAttribute('x1');
    const initialPositionY = chart.querySelector('.current-price-line')?.getAttribute('y1');
    fireEvent.keyDown(chart, { key: 'ArrowRight' });

    await waitFor(() => {
      expect(document.querySelector('.time-readout')?.textContent).toContain('2 / 3');
      expect(chart.querySelector('.chart-crosshair .crosshair-line')?.getAttribute('x1'))
        .not.toBe(initialCrosshairX);
      expect(screen.getByTestId('button-play-pause').getAttribute('aria-label'))
        .toBe('Play replay');
    });
    expect(api.advance).toHaveBeenCalledWith(expect.objectContaining({
      data: expect.objectContaining({ toIndex: 1 }),
    }));
    expect(chart.querySelector('.current-price-line')?.getAttribute('y1'))
      .not.toBe(initialPositionY);

    fireEvent.keyDown(chart, { key: 'ArrowLeft' });
    await waitFor(() => {
      expect(document.querySelector('.time-readout')?.textContent).toContain('1 / 3');
      expect(api.advance).toHaveBeenCalledTimes(2);
      expect(chart.querySelector('.chart-crosshair .crosshair-line')?.getAttribute('x1'))
        .toBe(initialCrosshairX);
    });
    expect(api.advance).toHaveBeenLastCalledWith(expect.objectContaining({
      data: expect.objectContaining({ toIndex: 0 }),
    }));

    fireEvent.keyDown(chart, { key: 'Escape' });
    expect(document.activeElement).not.toBe(chart);
    fireEvent.keyDown(document.body, { key: 'ArrowRight' });
    expect(api.advance).toHaveBeenCalledTimes(2);
  });

  it('keeps the cursor visible in a mocked replay while preserving post-To context', async () => {
    const allCandles = Array.from({ length: 180 }, (_, index) =>
      candle(
        new Date(Date.UTC(2023, 0, 1) + index * 15 * 60_000).toISOString(),
        2000 + index * 0.01,
      ),
    );
    const simulationCandles = allCandles.slice(0, 80);
    api.candles.mockImplementation((params: {
      symbol: InstrumentSymbol;
      timeframe: string;
      from: string;
      to: string;
      chunkMonth: string;
    }) => {
      const candles = params.to === '2023-01-02' ? simulationCandles : allCandles;
      return {
        data: {
          symbol: params.symbol,
          timeframe: params.timeframe,
          from: params.from,
          to: params.to,
          candles,
          totalCandles: candles.length,
          limit: 100000,
          truncated: false,
          chunks: [{ month: '2023-01', startIndex: 0, count: candles.length }],
          chunkMonth: params.chunkMonth,
          chunkOffset: 0,
          source: 'dukascopy',
          sourceSha256: 'a'.repeat(64),
        },
        isLoading: false,
        isFetching: false,
        isError: false,
        refetch: vi.fn(),
      };
    });
    api.start.mockResolvedValue({
      sessionId: 'session-follow-cursor',
      symbol: 'XAUUSD',
      runId: 'run-follow-cursor',
      timeframe: 'M15',
      swingDirectionVersion: 'V2',
      from: '2023-01-01',
      to: '2023-01-02',
      totalCandles: simulationCandles.length,
      cursor: 0,
      computedThrough: 0,
      states: [],
      wickPressureStates: [],
      simulation: null,
      currentCandle: simulationCandles[0],
    });
    api.advance.mockImplementation(async (request: {
      data: { toIndex: number };
    }) => {
      const index = request.data.toIndex;
      return {
        newStates: [],
        visibleStates: [],
        newWickPressureStates: [],
        visibleWickPressureStates: [],
        newAlerts: [],
        policyDecisions: [],
        newlyOpenedTrades: [],
        newlyResolvedTrades: [],
        simulation: null,
        computedThrough: index,
        hasMore: false,
        currentCandle: simulationCandles[index],
      };
    });

    render(<TooltipProvider><Home /></TooltipProvider>);
    fireEvent.change(screen.getByTestId('input-to-date'), {
      target: { value: '2023-01-02' },
    });
    fireEvent.click(screen.getByTestId('button-run-smoke'));
    await waitFor(() => expect(screen.getByTestId('status-run')).toBeTruthy());

    const window = screen.getByTestId('chart-visible-window');
    const target = simulationCandles.length - 1;
    const slider = screen.getByTestId('slider-replay-position');
    fireEvent.pointerDown(slider, { pointerId: 1 });
    fireEvent.change(slider, { target: { value: String(target) } });
    fireEvent.pointerUp(slider, { pointerId: 1 });
    await waitFor(() => {
      expect(Number(window.getAttribute('data-cursor-index'))).toBe(target);
    });

    const visibleStart = Number(window.getAttribute('data-visible-start'));
    const visibleEnd = Number(window.getAttribute('data-visible-end'));
    expect(visibleStart).toBeLessThanOrEqual(target);
    expect(visibleEnd).toBeGreaterThanOrEqual(target);
    expect(Number(window.getAttribute('data-display-end'))).toBe(allCandles.length - 1);
    expect(Number(window.getAttribute('data-simulation-end'))).toBe(simulationCandles.length - 1);
    expect(visibleEnd).toBeGreaterThan(simulationCandles.length - 1);
    expect(api.advance).toHaveBeenCalledWith(expect.objectContaining({
      data: expect.objectContaining({ toIndex: target }),
    }));
  });

  it('follows every mocked playback candle and renders its alerts and trade levels', async () => {
    const allCandles = Array.from({ length: 240 }, (_, index) =>
      candle(
        new Date(Date.UTC(2023, 0, 1) + index * 15 * 60_000).toISOString(),
        2000 + index * 0.01,
      ),
    );
    const simulationCandles = allCandles.slice(0, 192);
    api.candles.mockImplementation((params: {
      symbol: InstrumentSymbol;
      timeframe: string;
      from: string;
      to: string;
      chunkMonth: string;
    }) => {
      const candles = params.to === '2023-01-02' ? simulationCandles : allCandles;
      return {
        data: {
          symbol: params.symbol,
          timeframe: params.timeframe,
          from: params.from,
          to: params.to,
          candles,
          totalCandles: candles.length,
          limit: 100000,
          truncated: false,
          chunks: [{ month: '2023-01', startIndex: 0, count: candles.length }],
          chunkMonth: params.chunkMonth,
          chunkOffset: 0,
          source: 'dukascopy',
          sourceSha256: 'a'.repeat(64),
        },
        isLoading: false,
        isFetching: false,
        isError: false,
        refetch: vi.fn(),
      };
    });
    api.start.mockResolvedValue({
      sessionId: 'session-playback-follow',
      symbol: 'XAUUSD',
      runId: 'run-playback-follow',
      timeframe: 'M15',
      swingDirectionVersion: 'V2',
      from: '2023-01-01',
      to: '2023-01-02',
      totalCandles: simulationCandles.length,
      cursor: 0,
      computedThrough: 0,
      states: [],
      wickPressureStates: [],
      simulation: null,
      currentCandle: simulationCandles[0],
    });
    const playbackAlert = {
      ...alertPayload().alert,
      alertId: 'playback-alert',
      time: simulationCandles[2].time,
      symbol: 'XAUUSD' as const,
      candle: simulationCandles[2],
      previousCandle: simulationCandles[1],
    };
    const trade: SimulatedTrade = {
      tradeId: 'playback-open-trade',
      alertId: playbackAlert.alertId,
      symbol: 'XAUUSD',
      direction: 'BUY',
      sourceDirection: 'BUY',
      appliedDirection: 'BUY',
      directionMode: 'normal',
      invertScore: null,
      inverted: false,
      alertType: 'BUY-A',
      entryIndex: 3,
      entryTime: simulationCandles[3].time,
      entryPrice: simulationCandles[3].close,
      positionSize: 1,
      lotSize: 0.01,
      sizingMode: 'LOTS',
      contractSize: 100,
      pipSize: 0.1,
      pnlCurrency: 'USD',
      priceMove: 0,
      pipPnl: 0,
      riskAmount: 1,
      stopPrice: simulationCandles[3].close - 0.5,
      takeProfitPrice: simulationCandles[3].close + 0.5,
      exitModel: 'candle_geometry',
      geometryWeights: null,
      slInsideSweepTip: false,
      exitIndex: null,
      exitTime: null,
      exitPrice: null,
      outcome: 'OPEN',
      exitReason: null,
      grossPnl: 0,
      cost: 0,
      netPnl: 0,
      realizedR: 0,
      netR: 0,
    };
    api.advance.mockImplementation(async (request: { data: { toIndex: number } }) => {
      const index = request.data.toIndex;
      return {
        newStates: [],
        visibleStates: [],
        newWickPressureStates: [],
        visibleWickPressureStates: [],
        newAlerts: index === 2 ? [{ candleIndex: index, alert: playbackAlert }] : [],
        policyDecisions: [],
        newlyOpenedTrades: index === 3 ? [trade] : [],
        newlyResolvedTrades: [],
        simulation: {
          index,
          enabled: true,
          balance: 10000,
          openTradeCount: index >= 3 ? 1 : 0,
          closedTradeCount: 0,
          settings: {
            enabled: true,
            startingBalance: 1000,
            riskPercent: 1,
            sizingMode: 'LOTS',
            lotSize: 0.01,
            rewardMultiple: 2,
            stopBodyWeight: 1,
            stopWickMultiplier: 2,
            targetRangeMultiplier: 3,
            exitModel: 'candle_geometry',
          },
          stats: {
            closedTradeCount: 0,
            wins: 0,
            losses: 0,
            winRatePct: 0,
            avgR: 0,
            expectancyAfterCosts: 0,
            expectancyMoneyAfterCosts: 0,
            maxDrawdown: 0,
            maxDrawdownPct: 0,
            profitFactor: null,
          },
          equityCurve: [],
        },
        computedThrough: index,
        hasMore: false,
        currentCandle: simulationCandles[index],
      };
    });

    render(<TooltipProvider><Home /></TooltipProvider>);
    fireEvent.change(screen.getByTestId('input-to-date'), {
      target: { value: '2023-01-02' },
    });
    fireEvent.click(screen.getByTestId('button-run-smoke'));
    await waitFor(() => expect(screen.getByTestId('status-run')).toBeTruthy());

    const chartWindow = screen.getByTestId('chart-visible-window');
    const simulationEnd = simulationCandles.length - 1;
    let previousOhlc = screen.getByTestId('candle-ohlc').textContent;
    expect(screen.getAllByTestId('chart-candle')).toHaveLength(1);
    for (let zoom = 0; zoom < 8; zoom += 1) {
      fireEvent.click(screen.getByTestId('button-chart-zoom-in'));
    }
    fireEvent.click(screen.getByTestId('button-chart-latest'));
    expect(screen.getByTestId('button-chart-latest').getAttribute('aria-pressed')).toBe('true');
    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByTestId('button-play-pause'));
      for (let index = 1; index <= 15; index += 1) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(800);
        });
        expect(Number(chartWindow.getAttribute('data-cursor-index'))).toBe(index);
        expect(Number(chartWindow.getAttribute('data-visible-start'))).toBeLessThanOrEqual(index);
        expect(Number(chartWindow.getAttribute('data-visible-end'))).toBeGreaterThanOrEqual(index);
        expect(Number(chartWindow.getAttribute('data-display-end'))).toBe(allCandles.length - 1);
        expect(Number(chartWindow.getAttribute('data-simulation-end'))).toBe(simulationEnd);
        const visibleStart = Number(chartWindow.getAttribute('data-visible-start'));
        const visibleEnd = Number(chartWindow.getAttribute('data-visible-end'));
        const expectedVisibleCandles = Math.max(0, Math.min(index, visibleEnd) - visibleStart + 1);
        expect(screen.getAllByTestId('chart-candle')).toHaveLength(expectedVisibleCandles);

        const currentOhlc = screen.getByTestId('candle-ohlc').textContent;
        expect(currentOhlc).not.toBe(previousOhlc);
        previousOhlc = currentOhlc;

        const latestAdvance = api.advance.mock.calls.at(-1)?.[0] as {
          data: { toIndex: number; viewStart: number; viewEnd: number };
        };
        expect(latestAdvance.data.toIndex).toBe(index);
        expect(latestAdvance.data.viewStart).toBeLessThanOrEqual(index);
        expect(latestAdvance.data.viewEnd).toBeGreaterThanOrEqual(index);

        if (index === 2) {
          expect(screen.getByTestId('count-revealed-alerts').textContent).toBe('01');
          expect(screen.getByTestId('alert-row-playback-alert')).toBeTruthy();
          expect(screen.getByTestId('chart-alert-playback-alert')).toBeTruthy();
        }
        if (index === 3) {
          expect(
            screen.getByTestId('chart-replay').querySelector('.trade-entry-line'),
          ).toBeTruthy();
        }
      }

      fireEvent.click(screen.getByTestId('button-play-pause'));
      expect(api.advance).toHaveBeenCalledTimes(15);
      expect(Math.max(...api.advance.mock.calls.map(([request]) => request.data.toIndex)))
        .toBe(15);

      const chart = screen.getByTestId('chart-replay').querySelector('svg');
      if (!chart) throw new Error('Replay chart SVG was not rendered.');
      Object.defineProperty(chart, 'setPointerCapture', { value: vi.fn(), configurable: true });
      Object.defineProperty(chart, 'getBoundingClientRect', {
        value: () => ({
          left: 0,
          top: 0,
          right: 1100,
          bottom: 480,
          width: 1100,
          height: 480,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        }),
        configurable: true,
      });
      fireEvent.pointerDown(chart, {
        pointerId: 1,
        pointerType: 'mouse',
        button: 0,
        clientX: 100,
        clientY: 240,
      });
      fireEvent.pointerMove(chart, {
        pointerId: 1,
        pointerType: 'mouse',
        clientX: 900,
        clientY: 240,
      });
      fireEvent.pointerUp(chart, {
        pointerId: 1,
        pointerType: 'mouse',
        button: 0,
        clientX: 900,
        clientY: 240,
      });
      expect(screen.getByTestId('button-chart-latest').getAttribute('aria-pressed')).toBe('false');
      fireEvent.click(screen.getByTestId('button-chart-latest'));
      expect(screen.getByTestId('button-chart-latest').getAttribute('aria-pressed')).toBe('true');
      expect(Number(chartWindow.getAttribute('data-visible-start')))
        .toBeLessThanOrEqual(15);
      expect(Number(chartWindow.getAttribute('data-visible-end')))
        .toBeGreaterThanOrEqual(15);
    } finally {
      vi.useRealTimers();
    }
  }, 30_000);

  it('jumps back to a selected trade from the journal and marks it on the chart', async () => {
    const candles = Array.from({ length: 5 }, (_, index) =>
      candle(
        new Date(Date.UTC(2023, 0, 1) + index * 15 * 60_000).toISOString(),
        2000 + index,
      ),
    );
    const trade: SimulatedTrade = {
      tradeId: 'journal-jump-trade',
      alertId: 'journal-jump-alert',
      symbol: 'XAUUSD',
      direction: 'BUY',
      sourceDirection: 'BUY',
      appliedDirection: 'BUY',
      directionMode: 'normal',
      invertScore: null,
      inverted: false,
      alertType: 'BUY-A',
      entryIndex: 1,
      entryTime: candles[1].time,
      entryPrice: 2001,
      positionSize: 1,
      lotSize: 0.01,
      sizingMode: 'LOTS',
      contractSize: 100,
      pipSize: 0.1,
      pnlCurrency: 'USD',
      priceMove: 1,
      pipPnl: 10,
      riskAmount: 1,
      stopPrice: 2000,
      takeProfitPrice: 2002,
      exitModel: 'candle_geometry',
      geometryWeights: null,
      slInsideSweepTip: false,
      exitIndex: 3,
      exitTime: candles[3].time,
      exitPrice: 2002,
      outcome: 'WIN',
      exitReason: 'TP',
      grossPnl: 1,
      cost: 0,
      netPnl: 1,
      realizedR: 1,
      netR: 1,
    };
    api.candles.mockImplementation((params: {
      symbol: InstrumentSymbol;
      timeframe: string;
      from: string;
      to: string;
      chunkMonth: string;
    }) => ({
      data: {
        symbol: params.symbol,
        timeframe: params.timeframe,
        from: params.from,
        to: params.to,
        candles,
        totalCandles: candles.length,
        limit: 100000,
        truncated: false,
        chunks: [{ month: '2023-01', startIndex: 0, count: candles.length }],
        chunkMonth: params.chunkMonth,
        chunkOffset: 0,
        source: 'dukascopy',
        sourceSha256: 'a'.repeat(64),
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    }));
    api.start.mockResolvedValue({
      sessionId: 'session-journal',
      symbol: 'XAUUSD',
      runId: 'run-journal',
      timeframe: 'M15',
      swingDirectionVersion: 'V2',
      from: '2023-01-01',
      to: '2023-01-31',
      totalCandles: candles.length,
      cursor: 0,
      computedThrough: 0,
      states: [],
      wickPressureStates: [],
      simulation: null,
      currentCandle: candles[0],
    });
    api.advance.mockImplementation(async (request: { data: { toIndex: number } }) => {
      const index = request.data.toIndex;
      return {
        newStates: [],
        visibleStates: [],
        newWickPressureStates: [],
        visibleWickPressureStates: [],
        newAlerts: [],
        policyDecisions: [],
        newlyOpenedTrades: [],
        newlyResolvedTrades: index === 4 ? [trade] : [],
        simulation: null,
        computedThrough: index,
        hasMore: false,
        currentCandle: candles[index],
      };
    });

    render(<TooltipProvider><Home /></TooltipProvider>);
    fireEvent.click(screen.getByTestId('button-run-smoke'));
    await waitFor(() => expect(screen.getByTestId('status-run')).toBeTruthy());
    const window = screen.getByTestId('chart-visible-window');

    for (let index = 1; index <= 4; index += 1) {
      fireEvent.click(screen.getByTestId('button-next-candle'));
      await waitFor(() => {
        expect(Number(window.getAttribute('data-cursor-index'))).toBe(index);
      });
    }

    fireEvent.click(screen.getByTestId('tab-trade-journal'));
    const journalButton = await screen.findByTestId('journal-select-journal-jump-trade');
    fireEvent.click(journalButton);

    await waitFor(() => {
      expect(Number(window.getAttribute('data-cursor-index'))).toBe(3);
      expect(screen.getByTestId('journal-trade-focus')).toBeTruthy();
    });
    expect(api.start).toHaveBeenCalledTimes(2);
    expect(api.advance).toHaveBeenLastCalledWith(expect.objectContaining({
      data: expect.objectContaining({ toIndex: 3 }),
    }));
  });

  it('changes the selected month and loads the February chunk', async () => {
    render(<TooltipProvider><Home /></TooltipProvider>);

    fireEvent.change(screen.getByTestId('select-month'), { target: { value: '2023-02' } });
    expect(screen.getByTestId('status-month-progress').textContent)
      .toContain('Month 2/36 · 2023-02');
    await waitFor(() => expect(api.candles.mock.calls.some(
      ([params]) => params.chunkMonth === '2023-02',
    )).toBe(true));
  });

  it('defaults the simulation surface to the candle-geometry bracket', () => {
    render(<TooltipProvider><Home /></TooltipProvider>);

    expect((screen.getByTestId('select-simulation-exit-model') as HTMLSelectElement).value)
      .toBe('candle_geometry');
    expect((screen.getByTestId('input-simulation-stop-body-weight') as HTMLInputElement).value)
      .toBe('1');
    expect((screen.getByTestId('input-simulation-stop-wick-multiplier') as HTMLInputElement).value)
      .toBe('2');
    expect((screen.getByTestId('input-simulation-target-range-multiplier') as HTMLInputElement).value)
      .toBe('3');
    expect(screen.getByTestId('text-simulation-entry-fixed').textContent)
      .toContain('Entry: next candle open');
  });

  it('switches exit models and applies geometry settings to the next replay', async () => {
    render(<TooltipProvider><Home /></TooltipProvider>);

    const exitModel = screen.getByTestId('select-simulation-exit-model');
    fireEvent.change(exitModel, { target: { value: 'swept_level_atr' } });
    expect(screen.getByTestId('input-simulation-reward')).toBeTruthy();
    expect(screen.queryByTestId('input-simulation-stop-body-weight')).toBeNull();
    expect(screen.queryByTestId('input-simulation-stop-wick-multiplier')).toBeNull();
    expect(screen.queryByTestId('input-simulation-target-range-multiplier')).toBeNull();

    fireEvent.change(exitModel, { target: { value: 'candle_geometry' } });
    fireEvent.change(screen.getByTestId('input-simulation-stop-wick-multiplier'), {
      target: { value: '3' },
    });
    fireEvent.change(screen.getByTestId('input-simulation-target-range-multiplier'), {
      target: { value: '4' },
    });
    fireEvent.click(screen.getByTestId('button-apply-simulation-settings'));

    const runButton = screen.getByTestId('button-run-smoke');
    await waitFor(() => expect((runButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(runButton);
    await waitFor(() => expect(api.start).toHaveBeenCalledTimes(1));

    const request = api.start.mock.calls[0][0] as {
      data: {
        simulationSettings: {
          exitModel: string;
          stopWickMultiplier: number;
          targetRangeMultiplier: number;
        };
      };
    };
    expect(request.data.simulationSettings).toMatchObject({
      exitModel: 'candle_geometry',
      stopWickMultiplier: 3,
      targetRangeMultiplier: 4,
    });
  });

  it('uses each swing-family checkbox for its own labels and connector lines', () => {
    const candles = Array.from({ length: 8 }, (_, index) =>
      candle(new Date(Date.UTC(2025, 0, 1, 0, index * 15)).toISOString(), 2624 + index * 0.2),
    );
    const familySources = [
      { family: 'MAJOR', source: 'cst_major' },
      { family: 'MINOR', source: 'cct_minor' },
      { family: 'DC', source: 'dc' },
      { family: 'HIER', source: 'hier' },
    ] as const;
    const swingEvents = familySources.flatMap(({ source }) => [1, 5].map((pivotIndex, eventIndex) => {
      const confirmedIndex = eventIndex === 0 ? 2 : 6;
      return {
        type: 'swing_high',
        pivot_time: candles[pivotIndex].time,
        confirmed_time: candles[confirmedIndex].time,
        price: candles[pivotIndex].high,
        source,
        pivot_index: pivotIndex,
        confirmed_index: confirmedIndex,
        ...(source === 'hier' ? { level: 1 } : {}),
      };
    }));
    api.candles.mockImplementation((params: {
      symbol: InstrumentSymbol;
      timeframe: string;
      from: string;
      to: string;
      chunkMonth: string;
    }) => ({
      data: {
        symbol: params.symbol,
        timeframe: params.timeframe,
        from: params.from,
        to: params.to,
        candles,
        swingEvents,
        totalCandles: candles.length,
        limit: 100000,
        truncated: false,
        chunks: [{ month: params.chunkMonth, startIndex: 0, count: candles.length }],
        chunkMonth: params.chunkMonth,
        chunkOffset: 0,
        source: 'dukascopy',
        sourceSha256: 'a'.repeat(64),
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    }));
    render(<TooltipProvider><Home /></TooltipProvider>);
    fireEvent.click(screen.getByTestId('button-swing-version-v1'));
    fireEvent.click(screen.getByTestId('toggle-overlay-swings'));

    expect(document.querySelectorAll('.swing-marker')).toHaveLength(8);
    expect(document.querySelectorAll('.swing-connector')).toHaveLength(4);
    for (const { family, source } of familySources) {
      const checkbox = screen.getByTestId(
        `toggle-swing-family-${family.toLowerCase()}`,
      ) as HTMLInputElement;
      expect(checkbox.checked).toBe(true);
      fireEvent.click(checkbox);

      expect(document.querySelectorAll(`.swing-marker[data-source="${source}"]`))
        .toHaveLength(0);
      expect(document.querySelectorAll(`.swing-connector[data-family="${family}"]`))
        .toHaveLength(0);
      expect(document.querySelectorAll('.swing-marker')).toHaveLength(6);
      expect(document.querySelectorAll('.swing-connector')).toHaveLength(3);
      fireEvent.click(checkbox);
    }
  });

  it('keeps flat market candles visible beneath the current-price guide', () => {
    const flatCandle = {
      time: '2023-01-02T00:00:00Z',
      open: 2624.255,
      high: 2624.255,
      low: 2624.255,
      close: 2624.255,
      volume: 0,
    };
    api.candles.mockImplementation((params: {
      symbol: InstrumentSymbol;
      timeframe: string;
      from: string;
      to: string;
      chunkMonth: string;
    }) => ({
      data: {
        symbol: params.symbol,
        timeframe: params.timeframe,
        from: params.from,
        to: params.to,
        candles: [flatCandle, { ...flatCandle, time: '2023-01-02T00:15:00Z' }],
        totalCandles: 2,
        limit: 100000,
        truncated: false,
        chunks: [{ month: params.chunkMonth, startIndex: 0, count: 2 }],
        chunkMonth: params.chunkMonth,
        chunkOffset: 0,
        source: 'dukascopy',
        sourceSha256: 'a'.repeat(64),
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    }));
    render(<TooltipProvider><Home /></TooltipProvider>);

    expect(screen.getByTestId('status-flat-market').textContent)
      .toContain('Replay is continuing through flat historical candles.');
    const svg = screen.getByRole('img', { name: 'Historical XAUUSD candlestick replay chart' });
    const currentPriceLine = svg.querySelector('.current-price-line');
    const firstCandle = screen.getAllByTestId('chart-candle')[0];
    const body = firstCandle.querySelector('rect');
    expect(body?.getAttribute('fill')).toBe('var(--trend-neutral)');
    expect(Number(body?.getAttribute('height'))).toBeGreaterThanOrEqual(3.2);
    expect(
      currentPriceLine
        ? Boolean(currentPriceLine.compareDocumentPosition(firstCandle) & Node.DOCUMENT_POSITION_FOLLOWING)
        : false,
    ).toBe(true);
  });

  it('places indicator tracks below the chart time axis', () => {
    render(<TooltipProvider><Home /></TooltipProvider>);

    const chart = screen.getByRole('img', { name: 'Historical XAUUSD candlestick replay chart' });
    expect(chart.querySelectorAll('.x-label').length).toBeGreaterThan(0);

    const chartFrame = screen.getByTestId('chart-replay');
    const tracks = screen.getByTestId('indicator-track-strip');
    expect(Boolean(
      chartFrame.compareDocumentPosition(tracks) & Node.DOCUMENT_POSITION_FOLLOWING,
    )).toBe(true);
    expect(screen.getByTestId('indicator-track-chart').querySelector('[data-testid="cct-state-band"]'))
      .not.toBeNull();
  });

  it('keeps the selector usable and shows progress while a symbol loads', async () => {
    api.dataset.mockImplementation((params: { symbol: InstrumentSymbol }) => params.symbol === 'GBPUSD'
      ? {
          data: undefined,
          isLoading: true,
          isFetching: true,
          isError: false,
          refetch: vi.fn(),
        }
      : {
          data: datasetFor(params.symbol),
          isLoading: false,
          isFetching: false,
          isError: false,
          refetch: vi.fn(),
        });
    render(<TooltipProvider><Home /></TooltipProvider>);

    await screen.findByTestId('status-selected-symbol');
    const select = screen.getByTestId('select-symbol') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'GBPUSD' } });

    const loadingPanel = await screen.findByTestId('status-chart-loading');
    expect(loadingPanel.textContent ?? '').toContain('Loading GBPUSD market data…');
    expect(select.disabled).toBe(false);
    const goldOption = select.querySelector('option[value="XAUUSD"]') as HTMLOptionElement | null;
    expect(goldOption?.disabled).toBe(false);

    fireEvent.change(select, { target: { value: 'XAUUSD' } });
    await waitFor(() => expect(screen.getByTestId('status-selected-symbol').textContent).toBe('XAUUSD'));
    expect(screen.queryByTestId('status-chart-loading')).toBeNull();
  });

  it('reloads chart and replay alerts for the selected symbol', async () => {
    render(<TooltipProvider><Home /></TooltipProvider>);

    expect((await screen.findByTestId('status-selected-symbol')).textContent).toBe('XAUUSD');
    expect(screen.queryByRole('img', { name: 'Historical XAUUSD candlestick replay chart' })).not.toBeNull();

    api.start.mockResolvedValueOnce({
      sessionId: 'session-xau',
      symbol: 'XAUUSD',
      runId: 'run-xau',
      timeframe: 'M15',
      swingDirectionVersion: 'V2',
      from: '2023-01-01',
      to: '2023-01-31',
      totalCandles: 2,
      cursor: 0,
      computedThrough: 0,
      states: [],
      wickPressureStates: [],
      simulation: null,
      currentCandle: candle('2025-01-01T00:00:00Z', 2000),
    });
    fireEvent.click(screen.getByTestId('button-run-smoke'));
    await waitFor(() => expect(screen.getByTestId('status-run').textContent).toContain('INCREMENTAL SESSION'));

    fireEvent.change(screen.getByTestId('select-symbol'), { target: { value: 'GBPUSD' } });

    await waitFor(() => {
      expect(screen.getByTestId('selected-pair-label').textContent)
        .toBe('GBPUSD · BRITISH POUND / U.S. DOLLAR');
      expect(screen.getByTestId('chart-title').textContent).toContain('GBPUSD');
      expect(screen.getByTestId('status-selected-symbol').textContent).toBe('GBPUSD');
      expect(screen.getByTestId('evolve-scope').textContent).toBe('GBPUSD');
      expect(screen.queryByRole('img', { name: 'Historical GBPUSD candlestick replay chart' })).not.toBeNull();
    });
    const firstFxCandleBody = screen.getAllByTestId('chart-candle')[0]?.querySelector('rect');
    expect(Number(firstFxCandleBody?.getAttribute('height'))).toBeGreaterThan(3);
    expect(screen.queryByTestId('status-run')).toBeNull();
    expect(screen.queryByRole('img', { name: 'Historical XAUUSD candlestick replay chart' })).toBeNull();
    expect(window.sessionStorage.getItem('forex-strategy-lab-symbol')).toBe('GBPUSD');
    expect(api.candles.mock.calls.some(([params]) => params.symbol === 'GBPUSD')).toBe(true);

    fireEvent.click(screen.getByTestId('button-run-smoke'));
    await waitFor(() => expect(api.start).toHaveBeenCalledWith({
      data: expect.objectContaining({ symbol: 'GBPUSD' }),
    }));
    fireEvent.click(screen.getByTestId('button-next-candle'));
    const alertRow = await screen.findByTestId('alert-row-gbp-alert-1');
    expect(alertRow.textContent).toContain('GBPUSD · M15');
    expect(api.advance).toHaveBeenCalledWith(expect.objectContaining({
      sessionId: 'session-gbp',
    }));
  });

  it('clears an expired replay session so the next run can start fresh', async () => {
    const session = (sessionId: string) => ({
      sessionId,
      symbol: 'XAUUSD',
      runId: `run-${sessionId}`,
      timeframe: 'M15',
      swingDirectionVersion: 'V2',
      from: '2023-01-01',
      to: '2023-01-31',
      totalCandles: 2,
      cursor: 0,
      computedThrough: 0,
      states: [],
      wickPressureStates: [],
      simulation: null,
      currentCandle: candle('2023-01-02T00:00:00Z', 2000),
    });
    api.start
      .mockResolvedValueOnce(session('expired-session'))
      .mockResolvedValueOnce(session('fresh-session'));
    api.advance.mockRejectedValueOnce(
      Object.assign(new Error('Replay session expired.'), { status: 404 }),
    );

    render(<TooltipProvider><Home /></TooltipProvider>);
    fireEvent.click(await screen.findByTestId('button-run-smoke'));
    await waitFor(() => expect(screen.getByTestId('status-run').textContent)
      .toContain('INCREMENTAL SESSION'));

    fireEvent.click(screen.getByTestId('button-next-candle'));
    await waitFor(() => expect(screen.queryByTestId('status-run')).toBeNull());

    fireEvent.click(screen.getByTestId('button-run-smoke'));
    await waitFor(() => expect(api.start).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId('status-run').textContent).toContain('INCREMENTAL SESSION');
  });

  it('sends active Gold tape filters to trade simulation and updates the scope header', async () => {
    const matchingAlert = alertPayload();
    matchingAlert.alert.symbol = 'XAUUSD';
    matchingAlert.alert.alertType = 'BUY-B';
    matchingAlert.alert.engulfing = true;
    matchingAlert.alert.strengthLabel = 'STRONG';
    api.start.mockResolvedValueOnce({
      sessionId: 'session-gold-filters',
      symbol: 'XAUUSD',
      runId: 'run-gold-filters',
      timeframe: 'M15',
      swingDirectionVersion: 'V2',
      from: '2023-01-01',
      to: '2023-01-31',
      totalCandles: 2,
      cursor: 0,
      computedThrough: 0,
      states: [],
      wickPressureStates: [],
      simulation: null,
      currentCandle: candle('2023-01-02T00:00:00Z', 2000),
    });
    api.advance.mockResolvedValueOnce({
      newStates: [],
      visibleStates: [],
      newWickPressureStates: [{ index: 1, state: 'up-pressure' }],
      visibleWickPressureStates: [],
      newAlerts: [matchingAlert],
      policyDecisions: [],
      newlyOpenedTrades: [],
      newlyResolvedTrades: [],
      simulation: null,
      policySimulation: null,
      policyTrades: [],
      computedThrough: 1,
      hasMore: false,
      currentCandle: candle('2023-01-02T00:15:00Z', 2000.1),
    });

    render(<TooltipProvider><Home /></TooltipProvider>);
    await screen.findByTestId('filter-alert-strength');
    fireEvent.change(screen.getByTestId('filter-alert-strength'), {
      target: { value: 'STRONG' },
    });
    fireEvent.change(screen.getByTestId('filter-alert-direction'), {
      target: { value: 'BUY' },
    });
    fireEvent.change(screen.getByTestId('filter-alert-variant'), {
      target: { value: 'B' },
    });
    fireEvent.change(screen.getByTestId('filter-alert-wick-pressure'), {
      target: { value: 'up-pressure' },
    });
    fireEvent.change(screen.getByTestId('filter-alert-engulfing'), {
      target: { value: 'ENGULF' },
    });

    fireEvent.click(screen.getByTestId('button-run-smoke'));
    await waitFor(() => expect(screen.getByTestId('status-run').textContent)
      .toContain('INCREMENTAL SESSION'));
    fireEvent.click(screen.getByTestId('button-next-candle'));

    await screen.findByTestId(`alert-row-${matchingAlert.alert.alertId}`);
    expect(screen.getByTestId(`alert-row-${matchingAlert.alert.alertId}`).textContent)
      .toContain('STRONG');
    expect(screen.getByLabelText('Revealed alert counts').textContent)
      .toContain('STR 1');
    await waitFor(() => expect(screen.getByTestId('simulation-filter-scope').textContent)
      .toBe('Simulating: STRONG + BUY + VARIANT B + WP UP + ENGULF only · 1 of 1 alerts'));
    expect(api.advance).toHaveBeenCalledWith(expect.objectContaining({
      sessionId: 'session-gold-filters',
      data: {
        toIndex: 1,
        viewStart: 0,
        viewEnd: 1,
        simulationFilters: {
          direction: 'BUY',
          strength: 'STRONG',
          engulfingOnly: true,
          variant: 'B',
          wickPressure: 'up-pressure',
          mtfAgreementOnly: false,
        },
      },
    }));

    fireEvent.change(screen.getByTestId('filter-alert-strength'), {
      target: { value: 'ALL' },
    });
    expect(screen.getByTestId('simulation-filter-scope').textContent)
      .toBe('Simulating: BUY + VARIANT B + WP UP + ENGULF only · 1 of 1 alerts');
  });
});
