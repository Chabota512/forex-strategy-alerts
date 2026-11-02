import type { ConfirmedSwingSegment, SwingPlotBounds } from '@/lib/swing-events';
import { clipProjectedSwingSegment, projectSwingSegment } from '@/lib/swing-events';

type SwingConnectorLayerProps = {
  segments: ConfirmedSwingSegment[];
  viewStart: number;
  step: number;
  padLeft: number;
  bounds: SwingPlotBounds;
  yForPrice: (price: number) => number;
  formatPrice: (price: number) => string;
  renderLines?: boolean;
  renderTips?: boolean;
};

function edgeTick(point: { x: number; y: number }, other: { x: number; y: number }) {
  const dx = other.x - point.x;
  const dy = other.y - point.y;
  const length = Math.hypot(dx, dy) || 1;
  const halfLength = 3.5;
  const perpendicularX = -dy / length * halfLength;
  const perpendicularY = dx / length * halfLength;
  return {
    x1: point.x - perpendicularX,
    y1: point.y - perpendicularY,
    x2: point.x + perpendicularX,
    y2: point.y + perpendicularY,
  };
}

export function SwingConnectorLayer({
  segments,
  viewStart,
  step,
  padLeft,
  bounds,
  yForPrice,
  formatPrice,
  renderLines = true,
  renderTips = true,
}: SwingConnectorLayerProps) {
  return <>
    {segments.map(segment => {
      const projected = projectSwingSegment(segment, {
        viewStart,
        step,
        padLeft,
        yForPrice,
      });
      const clipped = clipProjectedSwingSegment(projected, bounds);
      if (!clipped) return null;

      const testId = `swing-connector-${segment.source}-${segment.start.pivot_index}-${segment.end.pivot_index}`;
      const side = (type: typeof segment.start.type) =>
        type === 'swing_high' ? 'HIGH' : 'LOW';
      const tooltip = `${segment.family} ${side(segment.start.type)} → ${side(segment.end.type)}: ${formatPrice(segment.start.price)} → ${formatPrice(segment.end.price)} · both confirmed`;
      const tip = (
        point: typeof clipped.start,
        other: typeof clipped.end,
        endpoint: 'start' | 'end',
      ) => point.atPivot
        ? <circle
          key={`${endpoint}-tip`}
          className="swing-connector-tip swing-connector-tip-pivot"
          data-testid={`${testId}-${endpoint}-tip`}
          data-tip-kind="pivot"
          cx={point.x}
          cy={point.y}
          r="2.5"
        />
        : <line
          key={`${endpoint}-tip`}
          className="swing-connector-tip swing-connector-tip-edge"
          data-testid={`${testId}-${endpoint}-tip`}
          data-tip-kind="edge"
          {...edgeTick(point, other)}
        />;

      return <g
        key={`${testId}-${segment.end.confirmed_index}`}
        className="swing-connector-group"
        data-testid={renderLines ? `${testId}-group` : undefined}
        data-source={segment.source}
        data-family={segment.family}
        data-start-type={segment.start.type}
        data-end-type={segment.end.type}
        role={renderLines ? 'img' : undefined}
        aria-hidden={renderLines ? undefined : true}
        aria-label={renderLines ? tooltip : undefined}
      >
        {renderLines && <>
          <title>{tooltip}</title>
          <line
            className="swing-connector"
            data-source={segment.source}
            data-family={segment.family}
            data-start-type={segment.start.type}
            data-end-type={segment.end.type}
            data-confirmed-index={segment.end.confirmed_index}
            data-testid={testId}
            x1={clipped.start.x}
            y1={clipped.start.y}
            x2={clipped.end.x}
            y2={clipped.end.y}
          />
        </>}
        {renderTips && <>
          {tip(clipped.start, clipped.end, 'start')}
          {tip(clipped.end, clipped.start, 'end')}
        </>}
      </g>;
    })}
  </>;
}
