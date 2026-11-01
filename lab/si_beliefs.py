"""Seeded belief rules, evidence ledger, and bounded parameter updates."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

if __package__:
    from .engine import (
        DEFAULT_STRENGTH_THRESHOLDS,
        DEFAULT_STRENGTH_WEIGHTS,
        STRENGTH_FEATURES,
        normalize_strength_thresholds,
        normalize_strength_weights,
    )
    from .simulation import (
        DEFAULT_INVERT_WEIGHTS,
        INVERT_FEATURES,
        inversion_feature_values,
        normalize_invert_weights,
    )
else:
    from engine import (
        DEFAULT_STRENGTH_THRESHOLDS,
        DEFAULT_STRENGTH_WEIGHTS,
        STRENGTH_FEATURES,
        normalize_strength_thresholds,
        normalize_strength_weights,
    )
    from simulation import (
        DEFAULT_INVERT_WEIGHTS,
        INVERT_FEATURES,
        inversion_feature_values,
        normalize_invert_weights,
    )

MIN_RULE_CASES = 5
MIN_TUNING_CASES = 20

RULE_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "id": "countertrend_consolidation",
        "name": "Counter-trend during consolidation",
        "description": "Alerts in consolidation or transition when MTF trend does not agree.",
    },
    {
        "id": "buy_after_consecutive_lows_reversal",
        "name": "Buy after consecutive-low reversal",
        "description": "BUY alerts after a confirmed V2 reversal following consecutive major lows.",
    },
    {
        "id": "fade_mtf_agreement",
        "name": "Fade side of MTF-agree cells",
        "description": "Mirror trades where the source alert agrees with the higher timeframe.",
    },
)


def _feature(case: Mapping[str, Any], key: str, default: Any = None) -> Any:
    features = case.get("features")
    return features.get(key, default) if isinstance(features, Mapping) else default


def matches_rule(rule_id: str, case: Mapping[str, Any]) -> bool:
    cct = str(_feature(case, "cct", "")).lower()
    cst = str(_feature(case, "cst_v2_state", _feature(case, "cst", ""))).lower()
    direction = str(_feature(case, "direction", "")).upper()
    mtf_agree = bool(_feature(case, "mtf_trend_agree", False))
    if rule_id == "countertrend_consolidation":
        is_consolidation = any(
            state in {"consolidation", "ranging", "range", "transition"}
            for state in (cct, cst)
        )
        return is_consolidation and not mtf_agree
    if rule_id == "buy_after_consecutive_lows_reversal":
        fingerprint = case.get("formation_fingerprint")
        fingerprint = fingerprint if isinstance(fingerprint, Mapping) else {}
        return (
            direction == "BUY"
            and bool(_feature(case, "swing_reversal", False))
            and bool(fingerprint.get("consecutive_lows_reversal"))
        )
    if rule_id == "fade_mtf_agreement":
        return case.get("case_type") == "fade" and mtf_agree
    return False


def initial_tunables() -> dict[str, Any]:
    return {
        "strength_weights": {
            key: float(value) for key, value in DEFAULT_STRENGTH_WEIGHTS.items()
        },
        "strength_thresholds": {
            key: float(value) for key, value in DEFAULT_STRENGTH_THRESHOLDS.items()
        },
        "invert_weights": {
            key: float(value) for key, value in DEFAULT_INVERT_WEIGHTS.items()
        },
    }


def validate_tunables(value: Mapping[str, Any]) -> dict[str, Any]:
    strength_weights = normalize_strength_weights(value.get("strength_weights"))
    strength_thresholds = normalize_strength_thresholds(
        value.get("strength_thresholds")
    )
    invert_weights = normalize_invert_weights(value.get("invert_weights"))
    return {
        "strength_weights": {
            key: float(item) for key, item in strength_weights.items()
        },
        "strength_thresholds": {
            key: float(item) for key, item in strength_thresholds.items()
        },
        "invert_weights": {
            key: float(item) for key, item in invert_weights.items()
        },
    }


def add_lesson_once(
    lessons: list[dict[str, Any]],
    *,
    lesson_id: str,
    rule_id: str,
    rule_name: str,
    action: str,
    evidence: Mapping[str, Any],
    created_at_utc: str,
    maximum: int = 200,
) -> bool:
    """Append an evidence-bearing lesson only once, retaining a bounded ledger."""
    if any(item.get("lesson_id") == lesson_id for item in lessons):
        return False
    lessons.append(
        {
            "lesson_id": lesson_id,
            "rule_id": rule_id,
            "rule_name": rule_name,
            "action": action,
            "evidence": dict(evidence),
            "created_at_utc": created_at_utc,
        }
    )
    del lessons[:-maximum]
    return True


def adjust_tunables(
    cases: Sequence[Mapping[str, Any]],
    tunables: Mapping[str, Any],
    *,
    case_count: int,
    last_tuned_case_count: int,
) -> tuple[dict[str, Any], int, list[str]]:
    """Make small whitelist-only updates using a bounded recent evidence window."""
    if (
        case_count < MIN_TUNING_CASES
        or case_count - last_tuned_case_count < 5
    ):
        return validate_tunables(tunables), last_tuned_case_count, []

    updated = validate_tunables(tunables)
    strength_weights = dict(updated["strength_weights"])
    strength_groups: dict[str, tuple[list[float], list[float]]] = {
        feature: ([], []) for feature in STRENGTH_FEATURES
    }
    scores: list[tuple[float, bool]] = []
    tuning_window = list(cases[-200:])
    for case in tuning_window:
        features = case.get("features", {})
        strength_features = (
            features.get("strength_features", features.get("strengthFeatures", {}))
            if isinstance(features, Mapping)
            else {}
        )
        if not isinstance(strength_features, Mapping):
            strength_features = {}
        outcome = float(case["net_r"])
        for feature in STRENGTH_FEATURES:
            try:
                value = float(strength_features.get(feature, 0))
            except (TypeError, ValueError):
                continue
            strength_groups[feature][0 if value > 0 else 1].append(outcome)
        try:
            score = float(
                features.get("strength_score", features.get("strengthScore", 0))
            )
            if math.isfinite(score):
                scores.append((score, outcome > 0))
        except (TypeError, ValueError):
            pass

    for feature, (positive, non_positive) in strength_groups.items():
        if not positive or not non_positive:
            continue
        difference = sum(positive) / len(positive) - sum(non_positive) / len(
            non_positive
        )
        if abs(difference) >= 0.05:
            strength_weights[feature] = max(
                0.0,
                min(
                    1.0,
                    strength_weights[feature]
                    + (0.01 if difference > 0 else -0.01),
                ),
            )
    if any(strength_weights.values()):
        normalized = normalize_strength_weights(strength_weights)
        updated["strength_weights"] = {
            key: float(value) for key, value in normalized.items()
        }

    thresholds = dict(updated["strength_thresholds"])
    if scores:
        baseline = sum(int(hit) for _, hit in scores) / len(scores)
        strong_samples = [
            hit for score, hit in scores if score >= thresholds["strong_min"]
        ]
        if len(strong_samples) >= 10:
            strong_rate = sum(int(hit) for hit in strong_samples) / len(strong_samples)
            if strong_rate < baseline - 0.05:
                thresholds["strong_min"] = min(1.0, thresholds["strong_min"] + 0.01)
            elif strong_rate > baseline + 0.05:
                thresholds["strong_min"] = max(0.01, thresholds["strong_min"] - 0.01)
        neutral_samples = [
            hit
            for score, hit in scores
            if thresholds["neutral_min"] <= score < thresholds["strong_min"]
        ]
        if len(neutral_samples) >= 10:
            neutral_rate = sum(int(hit) for hit in neutral_samples) / len(
                neutral_samples
            )
            if neutral_rate < baseline - 0.05:
                thresholds["neutral_min"] = min(
                    thresholds["strong_min"], thresholds["neutral_min"] + 0.01
                )
            elif neutral_rate > baseline + 0.05:
                thresholds["neutral_min"] = max(0.0, thresholds["neutral_min"] - 0.01)
    normalized_thresholds = normalize_strength_thresholds(thresholds)
    updated["strength_thresholds"] = {
        key: float(value) for key, value in normalized_thresholds.items()
    }

    pairs: dict[str, dict[str, Mapping[str, Any]]] = {}
    for case in tuning_window:
        features = case.get("features", {})
        features = features if isinstance(features, Mapping) else {}
        pair_key = json.dumps(
            {
                "alert_id": case.get("alert_id"),
                "source_id": case.get("source_id"),
                "sim_direction_mode": features.get("sim_direction_mode"),
                "sim_exit_model": features.get("sim_exit_model"),
                "sim_geometry_weights": features.get("sim_geometry_weights"),
                "swing_version": features.get("swing_version"),
                "strength_score": features.get("strength_score"),
                "strength_features": features.get(
                    "strength_features", features.get("strengthFeatures")
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        pairs.setdefault(pair_key, {})[str(case.get("case_type", ""))] = case
    matched_pairs = [
        (pair["real"], pair["fade"])
        for pair in pairs.values()
        if "real" in pair and "fade" in pair
    ]
    invert_weights = dict(updated["invert_weights"])
    if len(matched_pairs) >= 10:
        value_groups: dict[str, tuple[list[float], list[float]]] = {
            feature: ([], []) for feature in INVERT_FEATURES
        }
        for real_case, fade_case in matched_pairs:
            delta = float(fade_case["net_r"]) - float(real_case["net_r"])
            try:
                feature_values = inversion_feature_values(real_case["features"])
            except (KeyError, TypeError, ValueError):
                continue
            for feature in INVERT_FEATURES:
                value_groups[feature][0 if float(feature_values[feature]) > 0 else 1].append(
                    delta
                )
        for feature, (positive, non_positive) in value_groups.items():
            if not positive or not non_positive:
                continue
            advantage = sum(positive) / len(positive) - sum(non_positive) / len(
                non_positive
            )
            if abs(advantage) >= 0.05:
                invert_weights[feature] = max(
                    -1.0,
                    min(
                        1.0,
                        invert_weights[feature]
                        + (0.01 if advantage > 0 else -0.01),
                    ),
                )
        if any(invert_weights.values()):
            normalized_invert = normalize_invert_weights(invert_weights)
            updated["invert_weights"] = {
                key: float(value) for key, value in normalized_invert.items()
            }

    validated = validate_tunables(updated)
    changed = [
        key
        for key in ("strength_weights", "strength_thresholds", "invert_weights")
        if validated[key] != tunables[key]
    ]
    return validated, case_count, changed

