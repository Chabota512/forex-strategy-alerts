import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { SimulationSettings, SimulationSnapshot } from '@workspace/api-client-react';
import { SimulationPanel } from './simulation-panel';

const settings: SimulationSettings = {
  enabled: true,
  startingBalance: 10_000,
  riskPercent: 1,
  sizingMode: 'LOTS',
  lotSize: 0.01,
  rewardMultiple: 2,
  stopBodyWeight: 1,
  stopWickMultiplier: 2,
  targetRangeMultiplier: 3,
  exitModel: 'candle_geometry',
  geometryWeights: { bodyWeight: 1, wickMultiplier: 2, rangeMultiplier: 3 },
  directionMode: 'normal',
  invertWeights: {
    direction: 1 / 12,
    variant: 1 / 12,
    cctState: 1 / 12,
    cctAlignment: 1 / 12,
    cstState: 1 / 12,
    cstAlignment: 1 / 12,
    wpState: 1 / 12,
    mtfTrendAgree: 1 / 12,
    mtfWpAgree: 1 / 12,
    engulf: 1 / 12,
    dualTfAlert: 1 / 12,
    strengthScore: 1 / 12,
  },
  invertThreshold: 0.5,
  swingVersion: 'V2',
};

function snapshot(): SimulationSnapshot {
  return {
    enabled: true,
    balance: 10_125,
    openTradeCount: 0,
    closedTradeCount: 3,
    equityCurve: [
      { index: 0, equity: 10_000 },
      { index: 1, equity: 10_250 },
      { index: 2, equity: 10_125 },
    ],
    stats: {
      closedTradeCount: 3,
      wins: 2,
      losses: 1,
      winRatePct: 66.7,
      avgR: 0.8,
      expectancyAfterCosts: 0.5,
      maxDrawdown: 50,
      maxDrawdownPct: 0.5,
      profitFactor: 1.5,
    },
  } as unknown as SimulationSnapshot;
}

function renderPanel(onSettingsChange = vi.fn()) {
  return render(<SimulationPanel
    symbol="XAUUSD"
    simulationCost={0.35}
    settings={settings}
    snapshot={snapshot()}
    trades={[]}
    onSettingsChange={onSettingsChange}
    sessionActive
  />);
}

afterEach(cleanup);

describe('equity curve expansion', () => {
  it('updates the simulation scope label when the active filters change', () => {
    const onSettingsChange = vi.fn();
    const renderWithScope = (filterScopeLabel: string) => (
      <SimulationPanel
        symbol="XAUUSD"
        simulationCost={0.35}
        settings={settings}
        snapshot={snapshot()}
        trades={[]}
        filterScopeLabel={filterScopeLabel}
        onSettingsChange={onSettingsChange}
        sessionActive
      />
    );
    const { rerender } = render(renderWithScope(
      'Simulating: ALL alerts · 19,181 of 19,181 alerts',
    ));
    expect(screen.getByTestId('simulation-filter-scope').textContent)
      .toBe('Simulating: ALL alerts · 19,181 of 19,181 alerts');

    rerender(renderWithScope(
      'Simulating: STRONG only · 412 of 19,181 alerts',
    ));
    expect(screen.getByTestId('simulation-filter-scope').textContent)
      .toBe('Simulating: STRONG only · 412 of 19,181 alerts');
    expect(screen.getByText(/existing trades remain/)).not.toBeNull();
  });

  it('shows the active mode and swing version and applies direction changes', () => {
    const onSettingsChange = vi.fn();
    renderPanel(onSettingsChange);

    expect(screen.getByTestId('simulation-active-mode').textContent)
      .toBe('DIRECTION · NORMAL · SWING V2');
    fireEvent.change(screen.getByTestId('select-simulation-direction-mode'), {
      target: { value: 'inverted' },
    });
    fireEvent.click(screen.getByTestId('button-apply-simulation-settings'));
    expect(onSettingsChange).toHaveBeenCalledWith(
      expect.objectContaining({ directionMode: 'inverted' }),
    );
  });

  it('opens the full curve and closes from the close button', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('button-expand-equity-curve'));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Equity curve' })).not.toBeNull();
    const chart = within(dialog).getByTestId('chart-equity-curve-expanded');
    expect(chart).not.toBeNull();
    const curve = chart.querySelector('.equity-line');
    expect(curve).not.toBeNull();
    expect(curve?.getAttribute('points')?.trim().split(/\s+/)).toHaveLength(3);
    const yAxisLabels = Array.from(chart.querySelectorAll('.equity-axis-label'))
      .map(label => label.textContent ?? '')
      .filter(label => label.startsWith('$'));
    expect(new Set(yAxisLabels).size).toBe(5);
    expect(within(dialog).getByText('STARTING EQUITY')).not.toBeNull();
    expect(within(dialog).getByText('$10,250.00')).not.toBeNull();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('closes the expanded curve with Escape', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('button-expand-equity-curve'));
    const dialog = await screen.findByRole('dialog');

    fireEvent.keyDown(dialog, { key: 'Escape', code: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

describe('candle geometry decimal inputs', () => {
  it('preserves and applies decimals for all three multipliers', () => {
    const onSettingsChange = vi.fn();
    renderPanel(onSettingsChange);

    const decimalValues = [
      ['input-simulation-stop-body-weight', '1.25'],
      ['input-simulation-stop-wick-multiplier', '0.75'],
      ['input-simulation-target-range-multiplier', '2.5'],
    ] as const;

    for (const [testId, value] of decimalValues) {
      const input = screen.getByTestId(testId) as HTMLInputElement;
      fireEvent.change(input, { target: { value } });
      expect(input.value).toBe(value);
    }

    const applyButton = screen.getByTestId('button-apply-simulation-settings') as HTMLButtonElement;
    expect(applyButton.disabled).toBe(false);
    fireEvent.click(applyButton);
    expect(onSettingsChange).toHaveBeenCalledWith(expect.objectContaining({
      stopBodyWeight: 1.25,
      stopWickMultiplier: 0.75,
      targetRangeMultiplier: 2.5,
    }));
  });
});

describe('simulated trades table', () => {
  it('collapses and restores the table while keeping the record count visible', () => {
    const { container } = renderPanel();
    const tableWrap = container.querySelector('.sim-table-wrap') as HTMLDivElement;
    const headingToggle = screen.getByRole('button', { name: 'Collapse simulated trades table' });

    expect(headingToggle.getAttribute('aria-expanded')).toBe('true');
    expect(tableWrap.hidden).toBe(false);

    fireEvent.click(screen.getByText('Simulated trades'));
    expect(screen.getByRole('button', { name: 'Expand simulated trades table' }).getAttribute('aria-expanded')).toBe('false');
    expect(tableWrap.hidden).toBe(true);
    expect(screen.getByText('00 RECORDS')).not.toBeNull();

    fireEvent.click(screen.getByText('00 RECORDS'));
    expect(screen.getByRole('button', { name: 'Collapse simulated trades table' }).getAttribute('aria-expanded')).toBe('true');
    expect(tableWrap.hidden).toBe(false);
  });
});
