import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { InstrumentReviewInfo, InstrumentSymbol } from '@workspace/api-client-react';
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
}));

vi.mock('@workspace/api-client-react', () => ({
  getGetLabCandlesQueryKey: (params: unknown) => ['lab-candles', params],
  useGetLabDataset: (...args: unknown[]) => api.dataset(...args),
  useGetLabCandles: (...args: unknown[]) => api.candles(...args),
  useGetEvolvePolicy: (...args: unknown[]) => api.policy(...args),
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
  useGetAlertComment: () => ({
    mutate: api.comment,
    isPending: false,
    isError: false,
  }),
}));

vi.mock('@/components/evolve-control', () => ({
  default: ({ symbol }: { symbol: string }) => <output data-testid="evolve-scope">{symbol}</output>,
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
  window.sessionStorage.clear();
  api.policy.mockReturnValue({
    data: { mode: 'passthrough' },
    isLoading: false,
    isError: false,
  });
  api.dataset.mockImplementation((params: { symbol: InstrumentSymbol }) => ({
    data: datasetFor(params.symbol),
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
    const base = params.symbol === 'GBPUSD' ? 1.25 : 2000;
    const month = params.chunkMonth;
    const goldChunks = [
      { month: '2023-01', startIndex: 0, count: 1 },
      { month: '2023-02', startIndex: 1, count: 1 },
    ];
    const chunkOffset = params.symbol === 'XAUUSD' && month === '2023-02' ? 1 : 0;
    return {
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
  });
  api.start.mockResolvedValue({
    sessionId: 'session-gbp',
    symbol: 'GBPUSD',
    runId: 'run-gbp',
    timeframe: 'M15',
    from: '2025-01-01',
    to: '2025-12-31',
    totalCandles: 2,
    cursor: 0,
    computedThrough: 0,
    states: [],
    wickPressureStates: [],
    simulation: null,
    currentCandle: candle('2025-01-01T00:00:00Z', 1.25),
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
    expect(toggle.textContent).toBe('AI FILTER OFF');
    fireEvent.click(screen.getByTestId('button-run-smoke'));
    await waitFor(() => expect(screen.getByTestId('status-run').textContent).toContain('INCREMENTAL SESSION'));
    fireEvent.click(screen.getByTestId('button-next-candle'));

    expect(await screen.findByTestId('alert-row-hidden-alert')).toBeTruthy();
    expect(screen.getByTestId('alert-row-visible-alert')).toBeTruthy();
    fireEvent.click(toggle);

    expect(screen.queryByTestId('alert-row-hidden-alert')).toBeNull();
    expect(screen.getByTestId('alert-row-visible-alert')).toBeTruthy();
    expect(screen.getByTestId('ai-filter-hidden-summary').textContent).toBe(
      '1 hidden by AI filter',
    );
    fireEvent.click(screen.getByTestId('ai-filter-hidden-summary'));
    expect(screen.getByTestId('ai-filter-hidden').textContent).toContain(
      'min_strength=STRONG: alert strength is WEAK',
    );
  });

  it('opens gold in January 2023 with all six timeframes available', () => {
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
    for (const timeframe of ['m1', 'm5', 'm15', 'h1', 'h4', 'd1']) {
      expect(screen.getByTestId(`button-timeframe-${timeframe}`)).toBeTruthy();
    }
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
    expect(screen.getByTestId('crosshair-time-label').textContent).not.toBe('');
    expect(screen.getByTestId('crosshair-price-label').textContent).not.toBe('');

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
});
