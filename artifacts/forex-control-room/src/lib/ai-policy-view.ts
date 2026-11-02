export type PolicyDecisionView = {
  alertId: string;
  passed: boolean;
  reasons: string[];
};

export type PolicyAlertItem = {
  alert: {
    alertId: string;
    siScore?: number | null;
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
  minSiScore = 0,
): { visible: T[]; hidden: HiddenPolicyAlert<T>[] } {
  if (!enabled) return { visible: items, hidden: [] };

  const visible: T[] = [];
  const hidden: HiddenPolicyAlert<T>[] = [];
  for (const item of items) {
    const decision = decisions.get(item.alert.alertId);
    const policyPassed = policyMode !== 'active' || decision?.passed === true;
    const score = item.alert.siScore;
    const scorePassed = minSiScore <= 0
      || (typeof score === 'number' && Number.isFinite(score) && score >= minSiScore);
    if (policyPassed && scorePassed) {
      visible.push(item);
    } else {
      hidden.push({
        item,
        reasons: [
          ...(policyPassed ? [] : decision?.reasons.length
            ? decision.reasons
            : ['Policy decision unavailable for this alert.']),
          ...(!scorePassed
            ? [`SI score below ${minSiScore.toFixed(2)} or unavailable for the selected model.`]
            : []),
        ],
      });
    }
  }
  return { visible, hidden };
}
