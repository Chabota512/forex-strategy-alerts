import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { SimulatedTrade, TradeCommentResult } from '@workspace/api-client-react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TradeJournal } from './trade-journal';

const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock('@workspace/api-client-react', () => ({
  useGetTradeComment: () => ({ mutate, isPending: false }),
}));

function makeTrade(overrides: Partial<SimulatedTrade> = {}): SimulatedTrade {
  return {
    tradeId: 'trade-win',
    alertId: 'alert-win',
    symbol: 'XAUUSD',
    direction: 'BUY',
    sourceDirection: 'BUY',
    appliedDirection: 'BUY',
    directionMode: 'normal',
    invertScore: null,
    inverted: false,
    alertType: 'BUY-A',
    entryIndex: 10,
    entryTime: '2025-01-03T10:15:00Z',
    entryPrice: 2700,
    positionSize: 1,
    lotSize: 0.01,
    sizingMode: 'LOTS',
    contractSize: 100,
    pipSize: 0.1,
    pnlCurrency: 'USD',
    priceMove: 4,
    pipPnl: 40,
    riskAmount: 2,
    stopPrice: 2698,
    takeProfitPrice: 2704,
    exitModel: 'swept_level_atr',
    geometryWeights: null,
    slInsideSweepTip: false,
    exitIndex: 12,
    exitTime: '2025-01-03T10:17:00Z',
    exitPrice: 2704,
    outcome: 'WIN',
    exitReason: 'TP',
    grossPnl: 4,
    cost: 0.35,
    netPnl: 3.65,
    realizedR: 2,
    netR: 1.825,
    ...overrides,
  };
}

describe('trade journal', () => {
  beforeEach(() => mutate.mockReset());
  afterEach(cleanup);

  it('shows the inversion badge and score for an inverted trade', () => {
    const trade = makeTrade({
      direction: 'SELL',
      sourceDirection: 'BUY',
      appliedDirection: 'SELL',
      directionMode: 'inverted',
      invertScore: 0.625,
      inverted: true,
    });
    render(
      <TradeJournal
        trades={[trade]}
        symbol="XAUUSD"
        selectedTradeId={null}
        onSelect={() => undefined}
        systemIntelligenceEnabled={false}
      />,
    );

    expect(screen.getByTestId('journal-inverted-badge-trade-win').textContent)
      .toBe('INVERTED');
    expect(screen.getByTestId('journal-invert-score-trade-win').textContent)
      .toBe('SCORE 0.63');
  });

  it('filters outcomes, updates filtered statistics, and selects a trade', () => {
    const win = makeTrade();
    const loss = makeTrade({
      tradeId: 'trade-loss',
      alertId: 'alert-loss',
      direction: 'SELL',
      outcome: 'LOSS',
      exitReason: 'SL',
      netPnl: -2.35,
      pipPnl: -25,
      netR: -1.17,
    });
    const open = makeTrade({
      tradeId: 'trade-open',
      alertId: 'alert-open',
      outcome: 'OPEN',
      exitIndex: null,
      exitTime: null,
      exitPrice: null,
      exitReason: null,
      grossPnl: 0,
      cost: 0,
      netPnl: 0,
      realizedR: 0,
      netR: 0,
      priceMove: 0,
      pipPnl: 0,
    });
    const onSelect = vi.fn();

    render(
      <TradeJournal
        trades={[win, loss, open]}
        symbol="XAUUSD"
        selectedTradeId={null}
        onSelect={onSelect}
        systemIntelligenceEnabled={false}
      />,
    );

    fireEvent.click(screen.getByTestId('journal-filter-loss'));
    expect(screen.getByTestId('journal-trade-trade-loss')).toBeTruthy();
    expect(screen.queryByTestId('journal-trade-trade-win')).toBeNull();
    expect(screen.queryByTestId('journal-trade-trade-open')).toBeNull();
    const stats = screen.getByTestId('journal-filter-stats').textContent ?? '';
    expect(stats).toContain('1 CLOSED');
    expect(stats).toContain('0 W / 1 L');
    expect(stats).toContain('−USD 2.35');
    expect(stats).toContain('-1.17');

    fireEvent.click(screen.getByTestId('journal-select-trade-loss'));
    expect(onSelect).toHaveBeenCalledWith(loss);
  });

  it('requests a mocked AI note only after an explicit row action', () => {
    const trade = makeTrade();
    const result: TradeCommentResult = {
      available: true,
      tradeId: trade.tradeId,
      comment: 'The target closed above entry after a four-dollar move.',
      cached: true,
      tokenUsage: null,
      message: null,
      failureCategory: 'unknown',
    };
    render(
      <TradeJournal
        trades={[trade]}
        symbol="XAUUSD"
        selectedTradeId={null}
        onSelect={() => undefined}
        systemIntelligenceEnabled={false}
      />,
    );
    expect(mutate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('journal-ai-trade-win'));
    expect(mutate.mock.calls[0]?.[0]).toEqual({ data: { tradeId: trade.tradeId } });
    const call = mutate.mock.calls[0] as unknown as [
      unknown,
      { onSuccess: (value: TradeCommentResult) => void },
    ];
    act(() => call[1].onSuccess(result));
    expect(screen.getByTestId('journal-note-trade-win').textContent).toContain(result.comment);
    expect((screen.getByTestId('journal-ai-trade-win') as HTMLButtonElement).disabled).toBe(true);
  });

  it('keeps AI notes unavailable for open trades', () => {
    const trade = makeTrade({
      tradeId: 'trade-open',
      outcome: 'OPEN',
      exitIndex: null,
      exitTime: null,
      exitPrice: null,
      exitReason: null,
    });
    render(
      <TradeJournal
        trades={[trade]}
        symbol="XAUUSD"
        selectedTradeId={null}
        onSelect={() => undefined}
        systemIntelligenceEnabled={false}
      />,
    );
    expect((screen.getByTestId('journal-ai-trade-open') as HTMLButtonElement).disabled).toBe(true);
    expect(mutate).not.toHaveBeenCalled();
  });
});
