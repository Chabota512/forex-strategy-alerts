"""Local, evidence-gated rule proposals derived from resolved manual cases."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .knowledge import admit_candidate, admitted_knowledge
from .si_cases import DEFAULT_CASES_PATH, read_manual_cases

_RULE_FEATURES = (
    "direction",
    "alert_type",
    "cct",
    "cst",
    "wick_pressure",
    "engulfing",
    "mtf_trend_agree",
    "mtf_cct_agree",
    "mtf_wp_agree",
    "dual_tf_alert",
)


def propose_from_manual_cases(
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
) -> dict[str, Any]:
    resolved: list[dict[str, Any]] = []
    for row in read_manual_cases(cases_path):
        outcome = row.get("tradeOutcome")
        if (
            row.get("source") == "manual"
            and row.get("decision") == "take"
            and isinstance(outcome, dict)
            and outcome.get("netR") is not None
            and isinstance(row.get("featureContext"), dict)
        ):
            try:
                net_r = float(outcome["netR"])
            except (TypeError, ValueError):
                continue
            if net_r == net_r and abs(net_r) != float("inf"):
                resolved.append({**row, "_netR": net_r})
    if not resolved:
        return {
            "resolvedManualCases": 0,
            "minimumSampleCount": 30,
            "candidates": [],
            "admissions": [],
            "knowledge": admitted_knowledge("rule"),
        }
    baseline = sum(row["_netR"] for row in resolved) / len(resolved)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in resolved:
        context = row["featureContext"]
        for feature in _RULE_FEATURES:
            value = context.get(feature)
            if value is not None:
                groups[(feature, str(value))].append(row)
    candidates: list[dict[str, Any]] = []
    admissions: list[dict[str, Any]] = []
    for (feature, value), rows in sorted(groups.items()):
        if len(rows) < 30:
            continue
        expectancy = sum(row["_netR"] for row in rows) / len(rows)
        candidate = {"condition": {feature: value}, "source": "manual"}
        admission = admit_candidate(
            "rule",
            candidate,
            sample_count=len(rows),
            expectancy=expectancy,
            baseline_expectancy=baseline,
            provenance={
                "source": "manual",
                "caseIds": [str(row.get("caseId", "")) for row in rows],
                "resolvedCaseCount": len(resolved),
            },
        )
        admissions.append(admission)
        if admission["status"] == "admitted":
            candidates.append(
                {
                    **candidate,
                    "sampleCount": len(rows),
                    "expectancyR": expectancy,
                    "baselineExpectancyR": baseline,
                    "knowledgeKey": admission["key"],
                }
            )
    return {
        "resolvedManualCases": len(resolved),
        "minimumSampleCount": 30,
        "candidates": candidates,
        "admissions": admissions,
        "knowledge": admitted_knowledge("rule"),
    }
