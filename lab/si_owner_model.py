"""Evidence-shrunk model of owner take/skip decisions for XAUUSD alerts."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .si_manual_cases import (
    DEFAULT_MANUAL_CASES_PATH,
    read_manual_cases,
    read_manual_decisions,
)
from .simulation import INVERT_FEATURES, inversion_feature_values

MIN_OWNER_CASES = 30
EVIDENCE_PRIOR_CASES = 30
_LEARNING_RATE = 0.08
_L2 = 0.02


def _features(alert: Mapping[str, Any]) -> list[float]:
    values = inversion_feature_values(alert)
    return [float(values[key]) for key in INVERT_FEATURES]


def _empty_model() -> dict[str, Any]:
    return {
        "trained": False,
        "qualified": False,
        "caseCount": 0,
        "requiredCaseCount": MIN_OWNER_CASES,
        "intercept": 0.0,
        "weights": {key: 0.0 for key in INVERT_FEATURES},
        "effectiveWeights": {key: 0.0 for key in INVERT_FEATURES},
        "ownerWeight": 0.0,
        "evidenceWeight": 1.0,
        "hitRate": None,
        "expectancyR": None,
        "resolvedTakenCount": 0,
    }


def fit_owner_model(decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fit regularized logistic weights and shrink them toward evidence."""
    usable = [
        item
        for item in decisions
        if item.get("decision") in {"take", "skip"}
        and isinstance(item.get("featureContext"), Mapping)
    ]
    if not usable:
        return _empty_model()

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
            prediction = 1.0 / (
                1.0 + math.exp(-max(-30.0, min(30.0, raw)))
            )
            error = prediction - label
            intercept = max(
                -4.0, min(4.0, intercept - _LEARNING_RATE * error)
            )
            for index, feature in enumerate(features):
                weights[index] -= _LEARNING_RATE * (
                    error * feature + _L2 * weights[index]
                )
                weights[index] = max(-2.0, min(2.0, weights[index]))

    owner_weight = len(usable) / (len(usable) + EVIDENCE_PRIOR_CASES)
    resolved_taken: list[float] = []
    for item in usable:
        outcome = item.get("tradeOutcome")
        if item.get("decision") != "take" or not isinstance(outcome, Mapping):
            continue
        try:
            net_r = float(outcome.get("netR"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(net_r):
            resolved_taken.append(net_r)

    return {
        "trained": True,
        "qualified": len(usable) >= MIN_OWNER_CASES,
        "caseCount": len(usable),
        "requiredCaseCount": MIN_OWNER_CASES,
        "intercept": intercept,
        "weights": {
            key: float(weight)
            for key, weight in zip(INVERT_FEATURES, weights)
        },
        "effectiveWeights": {
            key: float(weight * owner_weight)
            for key, weight in zip(INVERT_FEATURES, weights)
        },
        "ownerWeight": owner_weight,
        "evidenceWeight": 1.0 - owner_weight,
        "hitRate": (
            sum(value > 0 for value in resolved_taken) / len(resolved_taken)
            if resolved_taken
            else None
        ),
        "expectancyR": (
            sum(resolved_taken) / len(resolved_taken)
            if resolved_taken
            else None
        ),
        "resolvedTakenCount": len(resolved_taken),
    }


def score_owner_choice(
    alert: Mapping[str, Any],
    model: Mapping[str, Any],
    evidence_score: float | None,
) -> float | None:
    if not model.get("trained"):
        return None
    weights = model.get("weights")
    if not isinstance(weights, Mapping):
        return None
    raw = float(model.get("intercept", 0.0)) + sum(
        float(weights.get(key, 0.0)) * value
        for key, value in zip(INVERT_FEATURES, _features(alert))
    )
    owner_probability = 1.0 / (
        1.0 + math.exp(-max(-30.0, min(30.0, raw)))
    )
    owner_weight = float(model.get("ownerWeight", 0.0))
    evidence_weight = float(model.get("evidenceWeight", 1.0))
    total = owner_weight + evidence_weight
    if total <= 0 or not 0 <= owner_weight <= 1 or not 0 <= evidence_weight <= 1:
        return None
    owner_weight /= total
    evidence_weight /= total
    evidence = (
        max(0.0, min(1.0, float(evidence_score)))
        if evidence_score is not None
        else 0.5
    )
    return max(
        0.0,
        min(1.0, owner_weight * owner_probability + evidence_weight * evidence),
    )


def select_score(
    alert: Mapping[str, Any],
    *,
    evidence_score: float | None,
    model_name: str,
    owner_model: Mapping[str, Any],
    blend_weight: float,
) -> tuple[float | None, float | None, float | None]:
    if model_name not in {"evidence", "owner", "blend"}:
        raise ValueError("SI model must be evidence, owner, or blend.")
    if not math.isfinite(float(blend_weight)) or not 0 <= blend_weight <= 1:
        raise ValueError("SI blend weight must be between 0 and 1.")
    evidence = (
        max(0.0, min(1.0, float(evidence_score)))
        if evidence_score is not None
        else None
    )
    owner = (
        score_owner_choice(alert, owner_model, evidence)
        if evidence is not None
        else None
    )
    if model_name == "evidence":
        return evidence, evidence, owner
    if model_name == "owner":
        return owner, evidence, owner
    if evidence is None or owner is None:
        return None, evidence, owner
    return (
        (1.0 - blend_weight) * evidence + blend_weight * owner,
        evidence,
        owner,
    )


def load_owner_model(
    path: Path = DEFAULT_MANUAL_CASES_PATH,
) -> dict[str, Any]:
    cases = {
        str(row.get("caseId", "")): row.get("tradeOutcome")
        for row in read_manual_cases(path)
    }
    decisions = [
        {
            **decision,
            "tradeOutcome": cases.get(str(decision.get("caseId", ""))),
        }
        for decision in read_manual_decisions(path)
    ]
    return fit_owner_model(decisions)
