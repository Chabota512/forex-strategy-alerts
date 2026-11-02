import { describe, expect, it } from 'vitest';
import { applyAiPolicyView, type PolicyAlertItem } from './ai-policy-view';

type TestItem = PolicyAlertItem & { candleIndex: number };

const items: TestItem[] = [
  { candleIndex: 1, alert: { alertId: 'kept' } },
  { candleIndex: 2, alert: { alertId: 'blocked' } },
];

describe('applyAiPolicyView', () => {
  it('shows every alert with the toggle off', () => {
    const result = applyAiPolicyView(
      items,
      false,
      'active',
      new Map([['blocked', { alertId: 'blocked', passed: false, reasons: ['min_strength=STRONG'] }]]),
    );
    expect(result.visible).toEqual(items);
    expect(result.hidden).toEqual([]);
  });

  it('hides rejected alerts with their policy reasons when enabled', () => {
    const result = applyAiPolicyView(
      items,
      true,
      'active',
      new Map([
        ['kept', { alertId: 'kept', passed: true, reasons: [] }],
        ['blocked', { alertId: 'blocked', passed: false, reasons: ['min_strength=STRONG'] }],
      ]),
    );
    expect(result.visible.map(item => item.alert.alertId)).toEqual(['kept']);
    expect(result.hidden).toEqual([
      { item: items[1], reasons: ['min_strength=STRONG'] },
    ]);
  });

  it('keeps passthrough policies transparent and fail-closed on missing decisions', () => {
    expect(applyAiPolicyView(items, true, 'passthrough', new Map()).visible).toEqual(items);
    const missing = applyAiPolicyView(items, true, 'active', new Map());
    expect(missing.visible).toEqual([]);
    expect(missing.hidden[0]?.reasons).toEqual([
      'Policy decision unavailable for this alert.',
    ]);
  });

  it('applies an owner-set score threshold under passthrough mode', () => {
    const scoredItems: TestItem[] = [
      { candleIndex: 1, alert: { alertId: 'kept', siScore: 0.8 } },
      { candleIndex: 2, alert: { alertId: 'blocked', siScore: 0.4 } },
    ];
    const filtered = applyAiPolicyView(
      scoredItems,
      true,
      'passthrough',
      new Map(),
      0.6,
    );
    expect(filtered.visible.map(item => item.alert.alertId)).toEqual(['kept']);
    expect(filtered.hidden[0]?.reasons).toEqual([
      'SI score below 0.60 or unavailable for the selected model.',
    ]);
    expect(applyAiPolicyView(items, true, 'passthrough', new Map(), 0).visible).toEqual(items);
  });
});
