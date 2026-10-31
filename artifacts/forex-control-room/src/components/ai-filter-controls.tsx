import type { HiddenPolicyAlert } from '@/lib/ai-policy-view';

type PolicySummaryItem = {
  alert: {
    alertId: string;
    direction: string;
    time: string;
  };
};

type AiFilterToggleProps = {
  enabled: boolean;
  onToggle: () => void;
};

type AiFilterHiddenSummaryProps<T extends PolicySummaryItem> = {
  hidden: HiddenPolicyAlert<T>[];
  formatTime: (value: string) => string;
};

export function AiFilterToggle({ enabled, onToggle }: AiFilterToggleProps) {
  return (
    <button
      type="button"
      className={`filter-ai${enabled ? ' active' : ''}`}
      aria-label="AI alert filter"
      aria-pressed={enabled}
      data-testid="filter-alert-ai"
      onClick={onToggle}
    >
      AI FILTER {enabled ? 'ON' : 'OFF'}
    </button>
  );
}

export function AiFilterHiddenSummary<T extends PolicySummaryItem>({
  hidden,
  formatTime,
}: AiFilterHiddenSummaryProps<T>) {
  if (hidden.length === 0) return null;

  return (
    <details className="ai-filter-hidden" data-testid="ai-filter-hidden">
      <summary data-testid="ai-filter-hidden-summary">
        {hidden.length} hidden by AI filter
      </summary>
      <ul className="ai-filter-hidden-list">
        {hidden.map(({ item, reasons }) => (
          <li className="ai-filter-hidden-item" key={item.alert.alertId}>
            <strong>
              {item.alert.direction} · {formatTime(item.alert.time)}
            </strong>
            <ul className="ai-filter-hidden-reasons">
              {reasons.map((reason, index) => (
                <li key={`${item.alert.alertId}-${index}`}>{reason}</li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </details>
  );
}
