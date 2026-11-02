import { describe, expect, it } from 'vitest';
import type { ConfirmedSwingEvent } from '@workspace/api-client-react';
import {
  projectSwingSegment,
  selectVisibleSwingEvents,
  selectVisibleSwingSegments,
  type SwingFamilySettings,
} from './swing-events';

const event = (
  source: ConfirmedSwingEvent['source'],
  pivot_index: number,
  confirmed_index: number,
  type: ConfirmedSwingEvent['type'] = 'swing_high',
): ConfirmedSwingEvent => ({
  type,
  pivot_time: `2025-01-01T00:${String(pivot_index).padStart(2, '0')}:00Z`,
  confirmed_time: `2025-01-01T00:${String(confirmed_index).padStart(2, '0')}:00Z`,
  price: type === 'swing_high' ? 100 + pivot_index : 80 + pivot_index,
  source,
  pivot_index,
  confirmed_index,
  ...(source === 'hier' ? { level: 1 } : {}),
});

const allFamilies = {
  MAJOR: true,
  MINOR: true,
  DC: true,
  HIER: true,
};

const familySources = [
  { family: 'MAJOR', source: 'cst_major' },
  { family: 'MINOR', source: 'cct_minor' },
  { family: 'DC', source: 'dc' },
  { family: 'HIER', source: 'hier' },
] as const;

const connectorFixture = familySources.flatMap(({ source }, familyIndex) => {
  const offset = familyIndex * 24;
  return [
    event(source, offset + 2, offset + 4, 'swing_high'),
    event(source, offset + 8, offset + 10, 'swing_low'),
    event(source, offset + 12, offset + 14, 'swing_high'),
    event(source, offset + 18, offset + 20, 'swing_low'),
  ];
});

const connectorOptions = (
  overrides: Partial<{
    showSwings: boolean;
    families: SwingFamilySettings;
    replayIndex: number;
    viewStart: number;
    viewSize: number;
  }> = {},
) => ({
  showSwings: true,
  families: allFamilies,
  replayIndex: 100,
  viewStart: 0,
  viewSize: 120,
  ...overrides,
});

describe('selectVisibleSwingEvents', () => {
  it('shows only confirmed pivots in the visible range and enabled families', () => {
    const events = [
      event('cct_minor', 10, 12),
      event('cst_major', 15, 20),
      event('dc', 18, 21),
      event('hier', 25, 30),
    ];
    const selected = selectVisibleSwingEvents(events, {
      showSwings: true,
      families: { ...allFamilies, DC: false },
      replayIndex: 20,
      viewStart: 10,
      viewSize: 12,
    });
    expect(selected).toEqual([events[0], events[1]]);
  });

  it('does not reveal an event before its confirmation candle', () => {
    const swing = event('cct_minor', 10, 12);
    const options = {
      showSwings: true,
      families: allFamilies,
      viewStart: 0,
      viewSize: 20,
    };
    expect(selectVisibleSwingEvents([swing], { ...options, replayIndex: 11 })).toEqual([]);
    expect(selectVisibleSwingEvents([swing], { ...options, replayIndex: 12 })).toEqual([swing]);
  });

  it('hides all labels when the SWINGS toggle is off', () => {
    expect(selectVisibleSwingEvents([event('hier', 5, 8)], {
      showSwings: false,
      families: allFamilies,
      replayIndex: 8,
      viewStart: 0,
      viewSize: 10,
    })).toEqual([]);
  });
});

describe('selectVisibleSwingSegments', () => {
  it('connects adjacent chronological swings into an alternating path', () => {
    for (const { family, source } of familySources) {
      const familyEvents = connectorFixture.filter(item => item.source === source);
      const segments = selectVisibleSwingSegments(
        [...familyEvents].reverse(),
        connectorOptions(),
      ).filter(segment => segment.family === family);

      expect(segments.map(segment => [
        segment.start.type,
        segment.end.type,
      ])).toEqual([
        ['swing_high', 'swing_low'],
        ['swing_low', 'swing_high'],
        ['swing_high', 'swing_low'],
      ]);
      expect(segments.map(segment => [
        segment.start.pivot_index,
        segment.end.pivot_index,
      ])).toEqual([
        [familyEvents[0].pivot_index, familyEvents[1].pivot_index],
        [familyEvents[1].pivot_index, familyEvents[2].pivot_index],
        [familyEvents[2].pivot_index, familyEvents[3].pivot_index],
      ]);
    }
  });

  it('waits for the next swing confirmation before drawing its segment', () => {
    for (const { family, source } of familySources) {
      const familyEvents = connectorFixture.filter(item => item.source === source);
      for (let index = 1; index < familyEvents.length; index += 1) {
        const start = familyEvents[index - 1];
        const end = familyEvents[index];
        const isSegment = (segment: {
          family: string;
          start: ConfirmedSwingEvent;
          end: ConfirmedSwingEvent;
        }) => segment.family === family
          && segment.start.pivot_index === start.pivot_index
          && segment.end.pivot_index === end.pivot_index;

        expect(selectVisibleSwingSegments(
          familyEvents,
          connectorOptions({ replayIndex: end.confirmed_index - 1 }),
        ).some(isSegment)).toBe(false);
        expect(selectVisibleSwingSegments(
          familyEvents,
          connectorOptions({ replayIndex: end.confirmed_index }),
        ).some(isSegment)).toBe(true);
      }
    }
  });

  it('keeps labels and connectors controlled by their own family checkbox', () => {
    for (const { family, source } of familySources) {
      const families = { ...allFamilies, [family]: false };
      const options = connectorOptions({ families });
      const labels = selectVisibleSwingEvents(connectorFixture, options);
      const segments = selectVisibleSwingSegments(connectorFixture, options);

      expect(labels.some(item => item.source === source)).toBe(false);
      expect(segments.some(item => item.family === family)).toBe(false);
      expect(labels.some(item => item.source !== source)).toBe(true);
      expect(segments.some(item => item.family !== family)).toBe(true);
    }

    const disabledOptions = connectorOptions({ showSwings: false });
    expect(selectVisibleSwingEvents(connectorFixture, disabledOptions)).toEqual([]);
    expect(selectVisibleSwingSegments(connectorFixture, disabledOptions)).toEqual([]);
  });

  it('matches batch connectors as stream events arrive at each closed candle', () => {
    const maxConfirmation = Math.max(...connectorFixture.map(item => item.confirmed_index));
    const streamed: ConfirmedSwingEvent[] = [];

    for (let replayIndex = 0; replayIndex <= maxConfirmation; replayIndex += 1) {
      streamed.push(...connectorFixture.filter(item => item.confirmed_index === replayIndex));
      const options = connectorOptions({ replayIndex });
      expect(selectVisibleSwingSegments(streamed, options)).toEqual(
        selectVisibleSwingSegments(connectorFixture, options),
      );
    }
  });

  it('projects both endpoints from their pivots after pan and zoom', () => {
    const segment = selectVisibleSwingSegments(
      [
        event('cst_major', 5, 7, 'swing_low'),
        event('cst_major', 12, 15, 'swing_high'),
      ],
      connectorOptions(),
    )[0];
    const yForPrice = (price: number) => 500 - price * 2;
    const projections = [
      projectSwingSegment(segment, {
        viewStart: 0,
        step: 10,
        padLeft: 16,
        yForPrice,
      }),
      projectSwingSegment(segment, {
        viewStart: 3,
        step: 10,
        padLeft: 16,
        yForPrice,
      }),
      projectSwingSegment(segment, {
        viewStart: 2,
        step: 20,
        padLeft: 16,
        yForPrice,
      }),
    ];

    for (const [index, projection] of projections.entries()) {
      const { viewStart, step } = [
        { viewStart: 0, step: 10 },
        { viewStart: 3, step: 10 },
        { viewStart: 2, step: 20 },
      ][index];
      expect((projection.x1 - 16) / step + viewStart - 0.5)
        .toBeCloseTo(segment.start.pivot_index);
      expect((projection.x2 - 16) / step + viewStart - 0.5)
        .toBeCloseTo(segment.end.pivot_index);
      expect(projection.y1).toBe(yForPrice(segment.start.price));
      expect(projection.y2).toBe(yForPrice(segment.end.price));
    }
  });
});
