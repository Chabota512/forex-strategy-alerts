import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ConfirmedSwingEvent } from '@workspace/api-client-react';
import { SwingConnectorLayer } from './swing-connector-layer';
import { selectVisibleSwingSegments } from '@/lib/swing-events';

afterEach(cleanup);

const swing = (
  pivot_index: number,
  price: number,
  type: ConfirmedSwingEvent['type'] = 'swing_high',
): ConfirmedSwingEvent => ({
  type,
  pivot_time: `2025-01-01T00:${String(pivot_index).padStart(2, '0')}:00Z`,
  confirmed_time: `2025-01-01T00:${String(pivot_index + 2).padStart(2, '0')}:00Z`,
  price,
  source: 'cst_major',
  pivot_index,
  confirmed_index: pivot_index + 2,
});

const options = {
  showSwings: true,
  families: { MAJOR: true, MINOR: true, DC: true, HIER: true },
  replayIndex: 30,
  viewStart: 10,
  viewSize: 5,
};

const renderConnectors = (events: ConfirmedSwingEvent[]) => {
  const segments = selectVisibleSwingSegments(events, options);
  return render(
    <svg>
      <SwingConnectorLayer
        segments={segments}
        viewStart={options.viewStart}
        step={10}
        padLeft={0}
        bounds={{ left: 0, right: 50, top: 0, bottom: 20 }}
        yForPrice={price => 100 - price}
        formatPrice={price => price.toFixed(3)}
      />
    </svg>,
  );
};

describe('swing connector endpoints', () => {
  it('renders an edge tick when one pivot is outside the visible window', () => {
    renderConnectors([swing(8, 97.046), swing(12, 96.264, 'swing_low')]);

    const edgeTip = screen.getByTestId(
      'swing-connector-cst_major-8-12-start-tip',
    );
    expect(edgeTip.tagName.toLowerCase()).toBe('line');
    expect(edgeTip.getAttribute('data-tip-kind')).toBe('edge');
    expect(screen.getByTestId('swing-connector-cst_major-8-12-end-tip')
      .getAttribute('data-tip-kind')).toBe('pivot');
  });

  it('renders dots at both visible pivots and the family, prices, and confirmation tooltip', () => {
    const { container } = renderConnectors([
      swing(11, 97.046),
      swing(13, 96.264, 'swing_low'),
    ]);

    const startTip = screen.getByTestId(
      'swing-connector-cst_major-11-13-start-tip',
    );
    const endTip = screen.getByTestId(
      'swing-connector-cst_major-11-13-end-tip',
    );
    expect(startTip.tagName.toLowerCase()).toBe('circle');
    expect(endTip.tagName.toLowerCase()).toBe('circle');
    expect(startTip.getAttribute('data-tip-kind')).toBe('pivot');
    expect(endTip.getAttribute('data-tip-kind')).toBe('pivot');
    expect(container.querySelector('title')?.textContent).toBe(
      'MAJOR HIGH → LOW: 97.046 → 96.264 · both confirmed',
    );
  });

  it('does not render a connector when both pivots are outside the window on the same side', () => {
    const { container } = renderConnectors([
      swing(2, 97.046),
      swing(6, 96.264, 'swing_low'),
    ]);

    expect(container.querySelector('[data-testid^="swing-connector-"]')).toBeNull();
  });
});
