"""Deterministic owner-choice model trained only on recorded manual decisions."""

from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from .si_cases import (
    DEFAULT_CASES_PATH,
    DEFAULT_OWNER_DECISIONS_PATH,
    read_manual_cases,
    read_manual_decisions,
)
from .knowledge import admitted_knowledge
from .simulation import INVERT_FEATURES, inversion_feature_values

MIN_OWNER_CASES = 30
EVIDENCE_PRIOR_CASES = 30
_LEARNING_RATE = 0.08
_L2 = 0.02


def _features(alert: Mapping[str, Any]) -> list[float]:
    values = inversion_feature_values(alert)
    return [float(values[key]) for key in INVERT_FEATURES]


def fit_owner_model(
    decisions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    usable = [
        item
        for item in decisions
        if item.get("decision") in {"take", "skip"}
        and isinstance(item.get("featureContext"), Mapping)
    ]
    if not usable:
        return {
            "trained": False,
            "caseCount": len(usable),
            "requiredCaseCount": MIN_OWNER_CASES,
            "qualified": False,
            "intercept": 0.0,
            "weights": {key: 0.0 for key in INVERT_FEATURES},
            "effectiveWeights": {key: 0.0 for key in INVERT_FEATURES},
            "ownerWeight": 0.0,
            "evidenceWeight": 1.0,
            "hitRate": None,
            "expectancyR": None,
            "resolvedTakenCount": 0,
        }
    weights = [0.0] * len(INVERT_FEATURES)
    intercept = 0.0
    samples = [
        (
            _features(item["featureContext"]),
            1.0 if item["decision"] == "take" else 0.0,
        )
        for item in usable
    ]
    for _ in range(220):
        for features, label in samples:
            raw = intercept + sum(
                weight * feature for weight, feature in zip(weights, features)
            )
            prediction = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, raw))))
            error = prediction - label
            intercept = max(-4.0, min(4.0, intercept - _LEARNING_RATE * error))
            for index, feature in enumerate(features):
                weights[index] -= _LEARNING_RATE * (
                    error * feature + _L2 * weights[index]
                )
                weights[index] = max(-2.0, min(2.0, weights[index]))
    owner_weight = len(usable) / (len(usable) + EVIDENCE_PRIOR_CASES)
    evidence_weight = 1.0 - owner_weight
    resolved_takes: list[float] = []
    for item in usable:
        outcome = item.get("tradeOutcome")
        if item.get("decision") != "take" or not isinstance(outcome, Mapping):
            continue
        net_r = outcome.get("netR")
        try:
            parsed_net_r = float(net_r)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed_net_r):
            resolved_takes.append(parsed_net_r)
    expectancy = (
        sum(resolved_takes) / len(resolved_takes) if resolved_takes else None
    )
    hit_rate = (
        sum(value > 0 for value in resolved_takes) / len(resolved_takes)
        if resolved_takes
        else None
    )
    return {
        "trained": True,
        "caseCount": len(usable),
        "requiredCaseCount": MIN_OWNER_CASES,
        "qualified": len(usable) >= MIN_OWNER_CASES,
        "intercept": intercept,
        "weights": {
            key: float(weight) for key, weight in zip(INVERT_FEATURES, weights)
        },
        "effectiveWeights": {
            key: float(max(-1.0, min(1.0, weight * owner_weight)))
            for key, weight in zip(INVERT_FEATURES, weights)
        },
        "ownerWeight": owner_weight,
        "evidenceWeight": evidence_weight,
        "hitRate": hit_rate,
        "expectancyR": expectancy,
        "resolvedTakenCount": len(resolved_takes),
    }


def score_owner_choice(
    alert: Mapping[str, Any],
    model: Mapping[str, Any],
) -> float | None:
    if not model.get("trained"):
        return None
    weights = model.get("weights")
    if not isinstance(weights, Mapping):
        return None
    features = _features(alert)
    raw = float(model.get("intercept", 0.0)) + sum(
        float(weights.get(key, 0.0)) * value
        for key, value in zip(INVERT_FEATURES, features)
    )
    raw_probability = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, raw))))
    owner_weight = float(model.get("ownerWeight", 1.0))
    evidence_weight = float(model.get("evidenceWeight", 0.0))
    if not 0 <= owner_weight <= 1 or not 0 <= evidence_weight <= 1:
        return None
    total = owner_weight + evidence_weight
    if total <= 0:
        return None
    owner_weight /= total
    evidence_weight /= total
    return owner_weight * raw_probability + evidence_weight * evidence_score(alert)


def load_owner_model(
    path: Path = DEFAULT_OWNER_DECISIONS_PATH,
    cases_path: Path = DEFAULT_CASES_PATH,
) -> dict[str, Any]:
    outcomes = {
        str(row.get("caseId", "")): row.get("tradeOutcome")
        for row in read_manual_cases(cases_path)
        if isinstance(row.get("tradeOutcome"), Mapping)
    }
    decisions = read_manual_decisions(path)
    joined = [
        {**decision, "tradeOutcome": outcomes.get(str(decision.get("caseId", "")))}
        for decision in decisions
    ]
    return fit_owner_model(joined)


def evidence_score(alert: Mapping[str, Any]) -> float:
    try:
        score = Decimal(str(alert.get("strength_score", 0)))
    except Exception:
        return 0.0
    return float(max(Decimal("0"), min(Decimal("1"), score)))


def selected_score(
    alert: Mapping[str, Any],
    *,
    model_name: str,
    owner_model: Mapping[str, Any],
    blend_weight: float | None,
) -> tuple[float | None, float, float | None]:
    evidence = evidence_score(alert)
    owner = score_owner_choice(alert, owner_model)
    if model_name == "evidence":
        return evidence, evidence, owner
    if model_name == "owner":
        return owner, evidence, owner
    if model_name == "blend":
        if owner is None:
            return None, evidence, None
        if blend_weight is None:
            accepted = admitted_knowledge("owner-weight")
            blend_weight = (
                float(accepted[-1]["candidate"].get("blendWeight", 0.5))
                if accepted
                else 0.5
            )
        if not 0 <= blend_weight <= 1:
            raise ValueError("SI blend weight must be between 0 and 1.")
        return (
            (1.0 - blend_weight) * evidence + blend_weight * owner,
            evidence,
            owner,
        )
    raise ValueError("SI model must be evidence, owner, or blend.")

