"""Aggregate-only System Intelligence rule proposal validation."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

PROPOSAL_CASE_THRESHOLD = 50
MIN_AGGREGATE_CLUSTER_SIZE = 30

ALLOWED_CONDITION_FIELDS = {
    "case_type": "category",
    "features.direction": "direction",
    "features.cct": "text",
    "features.cst_v2_state": "text",
    "features.mtf_trend_agree": "boolean",
    "features.swing_reversal": "boolean",
    "features.strength_label": "strength",
    "features.tf": "timeframe",
    "features.timeframe": "timeframe",
    "formation_fingerprint.consecutive_lows_reversal": "boolean",
}
_ALLOWED_OPERATORS = frozenset({"eq", "neq", "in"})
_CONSOLIDATION_STATES = ("consolidation", "ranging", "range", "transition")

def _canonical_json(value: Mapping[str, Any]) -> str:
    normalized = dict(value)
    for group_name in ("all", "any"):
        clauses = normalized.get(group_name)
        if isinstance(clauses, list):
            normalized[group_name] = sorted(
                clauses,
                key=lambda clause: json.dumps(
                    clause, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ),
            )
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def normalize_condition(value: Any) -> dict[str, Any]:
    """Validate the small, outcome-blind condition language and normalize it."""
    if not isinstance(value, Mapping) or not set(value).issubset({"all", "any"}):
        raise ValueError("condition must contain only all and/or any clauses.")
    if not value:
        raise ValueError("condition must contain at least one clause group.")
    result: dict[str, Any] = {}
    clause_count = 0
    for group_name in ("all", "any"):
        clauses = value.get(group_name)
        if clauses is None:
            continue
        if not isinstance(clauses, list) or not 1 <= len(clauses) <= 4:
            raise ValueError(f"condition.{group_name} must contain 1 to 4 clauses.")
        normalized_clauses = []
        for clause in clauses:
            if not isinstance(clause, Mapping) or set(clause) != {
                "field",
                "op",
                "value",
            }:
                raise ValueError("Each condition clause needs field, op, and value.")
            field_name = clause.get("field")
            operator = clause.get("op")
            if field_name not in ALLOWED_CONDITION_FIELDS:
                raise ValueError(f"Unsupported condition field: {field_name!r}.")
            if operator not in _ALLOWED_OPERATORS:
                raise ValueError("Condition op must be eq, neq, or in.")
            field_type = ALLOWED_CONDITION_FIELDS[str(field_name)]
            raw_value = clause.get("value")
            values = raw_value if operator == "in" else [raw_value]
            if operator == "in" and (
                not isinstance(raw_value, list) or not 2 <= len(raw_value) <= 8
            ):
                raise ValueError("Condition op=in requires 2 to 8 values.")
            checked_values = [
                _validate_condition_value(field_type, item) for item in values
            ]
            if operator == "in":
                if len({json.dumps(item, sort_keys=True) for item in checked_values}) != len(
                    checked_values
                ):
                    raise ValueError("Condition op=in values must be unique.")
                checked_values.sort(key=lambda item: json.dumps(item, sort_keys=True))
                normalized_value: Any = checked_values
            else:
                normalized_value = checked_values[0]
            normalized_clauses.append(
                {
                    "field": str(field_name),
                    "op": str(operator),
                    "value": normalized_value,
                }
            )
            clause_count += 1
        normalized_clauses.sort(
            key=lambda clause: json.dumps(
                clause, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
        )
        result[group_name] = normalized_clauses
    if clause_count > 4:
        raise ValueError("condition may contain at most 4 total clauses.")
    _canonical_json(result)
    return result


def _validate_condition_value(field_type: str, value: Any) -> Any:
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("Boolean condition fields require true or false.")
        return value
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 64:
        raise ValueError("Text condition values must be non-empty strings up to 64 characters.")
    normalized = value.strip()
    if field_type == "direction" and normalized not in {"BUY", "SELL"}:
        raise ValueError("Direction condition values must be BUY or SELL.")
    if field_type == "category" and normalized not in {"real", "fade"}:
        raise ValueError("case_type condition values must be real or fade.")
    if field_type == "strength" and normalized not in {"STRONG", "NEUTRAL", "WEAK"}:
        raise ValueError("Strength condition values must be STRONG, NEUTRAL, or WEAK.")
    if field_type == "timeframe" and normalized not in {
        "M1",
        "M5",
        "M15",
        "H1",
        "H4",
        "D1",
    }:
        raise ValueError("Timeframe condition value is not supported.")
    return normalized


def condition_signature(condition: Any) -> str:
    return _canonical_json(normalize_condition(condition))


def condition_matches(condition: Mapping[str, Any], case: Mapping[str, Any]) -> bool:
    """Evaluate validated clauses against a compact resolved case."""
    try:
        normalized = normalize_condition(condition)
    except ValueError:
        return False

    def clause_matches(clause: Mapping[str, Any]) -> bool:
        field_name = str(clause["field"])
        if field_name == "case_type":
            actual = case.get("case_type")
        else:
            prefix, key = field_name.split(".", 1)
            parent = (
                case.get("features")
                if prefix == "features"
                else case.get("formation_fingerprint")
            )
            actual = parent.get(key) if isinstance(parent, Mapping) else None
        operator = clause["op"]
        expected = clause["value"]
        if actual is None:
            return False
        if operator == "in":
            return any(type(actual) is type(item) and actual == item for item in expected)
        equal = type(actual) is type(expected) and actual == expected
        return equal if operator == "eq" else not equal

    return all(
        all(clause_matches(clause) for clause in normalized.get("all", []))
        and (
            not normalized.get("any")
            or any(clause_matches(clause) for clause in normalized["any"])
        )
        for _ in (0,)
    )


def validate_candidate(value: Any) -> dict[str, Any]:
    """Parse and validate a model candidate without accepting extra fields."""
    if not isinstance(value, Mapping):
        raise ValueError("proposal must be a JSON object.")
    if set(value) != {"name", "condition", "rationale"}:
        raise ValueError("proposal must contain only name, condition, and rationale.")
    name = value.get("name")
    rationale = value.get("rationale")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("name must be 1 to 80 characters.")
    if (
        not isinstance(rationale, str)
        or not rationale.strip()
        or len(rationale.strip()) > 500
    ):
        raise ValueError("rationale must be 1 to 500 characters.")
    condition = normalize_condition(value.get("condition"))
    signature = condition_signature(condition)
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return {
        "id": f"si-rule-{digest[:16]}",
        "name": name.strip(),
        "description": rationale.strip(),
        "rationale": rationale.strip(),
        "condition": condition,
        "condition_hash": digest,
    }


def parse_candidate(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Model response was not valid JSON.") from exc
    if isinstance(payload, Mapping) and set(payload) == {"candidate"}:
        payload = payload["candidate"]
    return validate_candidate(payload)


def candidate_duplicates(candidate: Mapping[str, Any], rules: Sequence[Mapping[str, Any]], lessons: Sequence[Mapping[str, Any]]) -> bool:
    signature = condition_signature(candidate.get("condition"))
    builtin_conditions = (
        {
            "all": [
                {"field": "case_type", "op": "eq", "value": "fade"},
                {"field": "features.mtf_trend_agree", "op": "eq", "value": True},
            ]
        },
        {
            "all": [
                {"field": "features.direction", "op": "eq", "value": "BUY"},
                {"field": "features.swing_reversal", "op": "eq", "value": True},
                {
                    "field": "formation_fingerprint.consecutive_lows_reversal",
                    "op": "eq",
                    "value": True,
                },
            ]
        },
        {
            "all": [
                {
                    "field": "features.mtf_trend_agree",
                    "op": "eq",
                    "value": False,
                }
            ],
            "any": [
                {
                    "field": "features.cct",
                    "op": "in",
                    "value": list(_CONSOLIDATION_STATES),
                },
                {
                    "field": "features.cst_v2_state",
                    "op": "in",
                    "value": list(_CONSOLIDATION_STATES),
                },
            ],
        },
    )
    if signature in {condition_signature(item) for item in builtin_conditions}:
        return True
    for rule in rules:
        condition = rule.get("condition")
        if isinstance(condition, Mapping):
            try:
                if condition_signature(condition) == signature:
                    return True
            except ValueError:
                continue
    for lesson in lessons:
        evidence = lesson.get("evidence")
        old_condition = (
            evidence.get("condition")
            if isinstance(evidence, Mapping)
            else None
        )
        if isinstance(old_condition, Mapping):
            try:
                if condition_signature(old_condition) == signature:
                    return True
            except ValueError:
                continue
        if lesson.get("condition_hash") == candidate.get("condition_hash"):
            return True
    return False


def build_aggregate_summary(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return aggregate counts and moments only; never expose case rows or IDs."""
    fields = (
        "case_type",
        "features.direction",
        "features.cct",
        "features.cst_v2_state",
        "features.mtf_trend_agree",
        "features.swing_reversal",
        "features.strength_label",
        "features.tf",
        "features.timeframe",
        "formation_fingerprint.consecutive_lows_reversal",
    )
    valid_cases: list[tuple[Mapping[str, Any], float]] = []
    for case in cases:
        try:
            net_r = float(case.get("net_r"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(net_r):
            valid_cases.append((case, net_r))
    if not valid_cases:
        return {
            "resolvedCaseCount": 0,
            "hitRate": None,
            "expectancyR": None,
            "minimumClusterSize": MIN_AGGREGATE_CLUSTER_SIZE,
            "clusters": [],
        }

    total_hits = sum(net_r > 0 for _, net_r in valid_cases)
    total_r = sum(net_r for _, net_r in valid_cases)
    groups: dict[tuple[str, str], list[tuple[Mapping[str, Any], float]]] = defaultdict(list)
    for case, net_r in valid_cases:
        features = case.get("features")
        fingerprint = case.get("formation_fingerprint")
        for field_name in fields:
            if field_name == "case_type":
                source, key = case, "case_type"
            else:
                prefix, key = field_name.split(".", 1)
                source = features if prefix == "features" else fingerprint
            if not isinstance(source, Mapping) or key not in source:
                continue
            value = source[key]
            if isinstance(value, bool):
                display = value
            elif isinstance(value, str) and value.strip():
                display = value.strip()
            else:
                continue
            groups[(field_name, json.dumps(display, sort_keys=True))].append(
                (case, net_r)
            )

    pairs_by_field: dict[tuple[str, str], list[float]] = defaultdict(list)
    pairs: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for case, net_r in valid_cases:
        alert_id = str(case.get("alert_id", ""))
        case_type = str(case.get("case_type", ""))
        if alert_id and case_type in {"real", "fade"}:
            pairs[alert_id][case_type] = case
    for paired in pairs.values():
        if "real" not in paired or "fade" not in paired:
            continue
        real = paired["real"]
        fade = paired["fade"]
        try:
            delta = float(fade.get("net_r")) - float(real.get("net_r"))
        except (TypeError, ValueError):
            continue
        features = real.get("features")
        if not isinstance(features, Mapping):
            continue
        for field_name in fields:
            if field_name == "case_type":
                source, key = real, "case_type"
            else:
                prefix, key = field_name.split(".", 1)
                source = (
                    features
                    if prefix == "features"
                    else real.get("formation_fingerprint")
                )
            if not isinstance(source, Mapping) or key not in source:
                continue
            value = source[key]
            if isinstance(value, (str, bool)) and (not isinstance(value, str) or value.strip()):
                pairs_by_field[(field_name, json.dumps(value, sort_keys=True))].append(delta)

    clusters = []
    for (field_name, encoded_value), members in sorted(groups.items()):
        if len(members) < MIN_AGGREGATE_CLUSTER_SIZE:
            continue
        value = json.loads(encoded_value)
        hits = sum(net_r > 0 for _, net_r in members)
        fingerprint_true = sum(
            bool(
                isinstance(case.get("formation_fingerprint"), Mapping)
                and case["formation_fingerprint"].get("consecutive_lows_reversal") is True
            )
            for case, _ in members
        )
        fingerprint_false = sum(
            bool(
                isinstance(case.get("formation_fingerprint"), Mapping)
                and case["formation_fingerprint"].get("consecutive_lows_reversal") is False
            )
            for case, _ in members
        )
        deltas = pairs_by_field.get((field_name, encoded_value), [])
        clusters.append(
            {
                "condition": {
                    "all": [{"field": field_name, "op": "eq", "value": value}]
                },
                "sampleCount": len(members),
                "hitRate": round(hits / len(members), 6),
                "expectancyR": round(sum(net_r for _, net_r in members) / len(members), 6),
                "fadeVsFollow": {
                    "pairedAlerts": len(deltas),
                    "meanFadeMinusFollowR": (
                        round(sum(deltas) / len(deltas), 6) if deltas else None
                    ),
                },
                "fingerprintPatternFrequency": {
                    "consecutiveLowsReversal": {
                        "true": fingerprint_true,
                        "false": fingerprint_false,
                    }
                },
            }
        )
    return {
        "resolvedCaseCount": len(valid_cases),
        "hitRate": round(total_hits / len(valid_cases), 6),
        "expectancyR": round(total_r / len(valid_cases), 6),
        "minimumClusterSize": MIN_AGGREGATE_CLUSTER_SIZE,
        "clusters": clusters,
    }


def build_proposal_prompt(aggregates: Mapping[str, Any]) -> str:
    """Prompt Gemini with aggregate metrics only, with an explicit data boundary."""
    return (
        "Propose one informational System Intelligence belief rule for historical "
        "XAUUSD simulation research. This proposal is never auto-accepted and must "
        "not recommend orders. Use only the aggregate JSON below. It contains no "
        "raw cases, rows, IDs, or timestamps. Do not infer or request any raw data. "
        "Only use these exact condition fields: case_type, features.direction, "
        "features.cct, features.cst_v2_state, features.mtf_trend_agree, "
        "features.swing_reversal, features.strength_label, features.tf, "
        "formation_fingerprint.consecutive_lows_reversal. Condition operators "
        "are eq, neq, and in. Return exactly one JSON object with keys name, "
        "condition, rationale. Condition must be {\"all\":[clauses]} and may also "
        "include an \"any\" list; each clause is {\"field\":...,\"op\":...,"
        "\"value\":...}. Do not use outcomes, expectancy, hit-rate, or P&L as "
        "condition fields. Keep rationale concise.\nAGGREGATES:\n"
        + json.dumps(aggregates, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )
