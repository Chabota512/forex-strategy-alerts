export type PolicyDecisionView = {
  alertId: string;
  passed: boolean;
  reasons: string[];
};

export type PolicyAlertItem = {
  alert: {
    alertId: string;
  };
};

export type HiddenPolicyAlert<T> = {
  item: T;
  reasons: string[];
};

export function applyAiPolicyView<T extends PolicyAlertItem>(
  items: T[],
  enabled: boolean,
  policyMode: string | undefined,
  decisions: ReadonlyMap<string, PolicyDecisionView>,
): { visible: T[]; hidden: HiddenPolicyAlert<T>[] } {
  if (!enabled || policyMode !== 'active') {
    return { visible: items, hidden: [] };
  }

  const visible: T[] = [];
  const hidden: HiddenPolicyAlert<T>[] = [];
  for (const item of items) {
    const decision = decisions.get(item.alert.alertId);
    if (decision?.passed === true) {
      visible.push(item);
    } else {
      hidden.push({
        item,
        reasons: decision?.reasons.length
          ? decision.reasons
          : ['Policy decision unavailable for this alert.'],
      });
    }
  }
  return { visible, hidden };
}
