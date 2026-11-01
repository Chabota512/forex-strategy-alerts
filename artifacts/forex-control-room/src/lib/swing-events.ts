import type { ConfirmedSwingEvent } from '@workspace/api-client-react';

export type SwingFamily = 'MAJOR' | 'MINOR' | 'DC' | 'HIER';
export type SwingFamilySettings = Record<SwingFamily, boolean>;

export type ConfirmedSwingSegment = {
  source: ConfirmedSwingEvent['source'];
  family: SwingFamily;
  start: ConfirmedSwingEvent;
  end: ConfirmedSwingEvent;
};

const SOURCE_FAMILY: Record<ConfirmedSwingEvent['source'], SwingFamily> = {
  cct_minor: 'MINOR',
  cst_major: 'MAJOR',
  dc: 'DC',
  hier: 'HIER',
};

const SOURCE_ORDER: Record<ConfirmedSwingEvent['source'], number> = {
  cct_minor: 0,
  cst_major: 1,
  dc: 2,
  hier: 3,
};

export function selectVisibleSwingEvents(
  events: ConfirmedSwingEvent[],
  options: {
    showSwings: boolean;
    families: SwingFamilySettings;
    replayIndex: number;
    viewStart: number;
    viewSize: number;
  },
): ConfirmedSwingEvent[] {
  if (!options.showSwings) return [];
  const viewEnd = options.viewStart + options.viewSize;
  return events
    .filter(event =>
      event.confirmed_index <= options.replayIndex
      && event.pivot_index >= options.viewStart
      && event.pivot_index < viewEnd
      && options.families[SOURCE_FAMILY[event.source]],
    )
    .sort((left, right) =>
      left.confirmed_index - right.confirmed_index
      || left.pivot_index - right.pivot_index
      || SOURCE_ORDER[left.source] - SOURCE_ORDER[right.source],
    );
}

export function selectVisibleSwingSegments(
  events: ConfirmedSwingEvent[],
  options: {
    showSwings: boolean;
    families: SwingFamilySettings;
    replayIndex: number;
    viewStart: number;
    viewSize: number;
  },
): ConfirmedSwingSegment[] {
  if (!options.showSwings || options.viewSize <= 0) return [];

  const tracks = new Map<string, {
    source: ConfirmedSwingEvent['source'];
    family: SwingFamily;
    events: ConfirmedSwingEvent[];
  }>();
  for (const event of events) {
    const family = SOURCE_FAMILY[event.source];
    if (!options.families[family]) continue;
    const key = event.source;
    const track = tracks.get(key) ?? {
      source: event.source,
      family,
      events: [],
    };
    track.events.push(event);
    tracks.set(key, track);
  }

  const viewEnd = options.viewStart + options.viewSize;
  const segments: ConfirmedSwingSegment[] = [];
  for (const track of tracks.values()) {
    const confirmed = track.events.sort((left, right) =>
      left.pivot_index - right.pivot_index
      || left.confirmed_index - right.confirmed_index
      || (left.level ?? 0) - (right.level ?? 0),
    );
    for (let index = 1; index < confirmed.length; index += 1) {
      const start = confirmed[index - 1];
      const end = confirmed[index];
      if (
        start.confirmed_index > options.replayIndex
        || end.confirmed_index > options.replayIndex
      ) continue;
      if (start.pivot_index === end.pivot_index) continue;
      const firstPivot = Math.min(start.pivot_index, end.pivot_index);
      const lastPivot = Math.max(start.pivot_index, end.pivot_index);
      if (lastPivot < options.viewStart || firstPivot >= viewEnd) continue;
      segments.push({
        source: track.source,
        family: track.family,
        start,
        end,
      });
    }
  }

  return segments.sort((left, right) =>
    SOURCE_ORDER[left.source] - SOURCE_ORDER[right.source]
    || left.end.confirmed_index - right.end.confirmed_index
    || left.end.pivot_index - right.end.pivot_index,
  );
}

export function projectSwingSegment(
  segment: ConfirmedSwingSegment,
  options: {
    viewStart: number;
    step: number;
    padLeft: number;
    yForPrice: (price: number) => number;
  },
) {
  const pivotX = (event: ConfirmedSwingEvent) =>
    options.padLeft + options.step * (event.pivot_index - options.viewStart + 0.5);
  return {
    x1: pivotX(segment.start),
    y1: options.yForPrice(segment.start.price),
    x2: pivotX(segment.end),
    y2: options.yForPrice(segment.end.price),
  };
}

export type SwingPlotBounds = {
  left: number;
  right: number;
  top: number;
  bottom: number;
};

export type ClippedSwingSegment = {
  start: { x: number; y: number; atPivot: boolean };
  end: { x: number; y: number; atPivot: boolean };
};

export function clipProjectedSwingSegment(
  coordinates: ReturnType<typeof projectSwingSegment>,
  bounds: SwingPlotBounds,
): ClippedSwingSegment | null {
  const dx = coordinates.x2 - coordinates.x1;
  const dy = coordinates.y2 - coordinates.y1;
  let tStart = 0;
  let tEnd = 1;

  const clipEdge = (p: number, q: number) => {
    if (p === 0) return q >= 0;
    const ratio = q / p;
    if (p < 0) {
      if (ratio > tEnd) return false;
      tStart = Math.max(tStart, ratio);
    } else {
      if (ratio < tStart) return false;
      tEnd = Math.min(tEnd, ratio);
    }
    return true;
  };

  if (
    !clipEdge(-dx, coordinates.x1 - bounds.left)
    || !clipEdge(dx, bounds.right - coordinates.x1)
    || !clipEdge(-dy, coordinates.y1 - bounds.top)
    || !clipEdge(dy, bounds.bottom - coordinates.y1)
    || tStart > tEnd
  ) return null;

  const pointAt = (t: number, atPivot: boolean) => ({
    x: coordinates.x1 + t * dx,
    y: coordinates.y1 + t * dy,
    atPivot,
  });
  return {
    start: pointAt(tStart, tStart === 0),
    end: pointAt(tEnd, tEnd === 1),
  };
}
