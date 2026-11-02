"""Append-only owner decision and resolved-case records for System Intelligence."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Mapping

DEFAULT_CASES_PATH = Path(__file__).resolve().parent / "output" / "si_cases.jsonl"
DEFAULT_OWNER_DECISIONS_PATH = (
    Path(__file__).resolve().parent / "output" / "owner_decisions.jsonl"
)

_LOCK = threading.RLock()
_FINGERPRINT_FIELDS = (
    "direction",
    "alert_type",
    "cct",
    "cst",
    "cst_v1_state",
    "cst_v2_state",
    "wick_pressure",
    "engulfing",
    "mtf_trend_agree",
    "mtf_cct_agree",
    "mtf_wp_agree",
    "dual_tf_alert",
    "strength_score",
    "strength_features",
    "swing_version_agreement_pct",
    "legacy_agreement_pct",
    "candle",
    "previous_candle",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def formation_fingerprint(alert: Mapping[str, Any]) -> str:
    """Hash stable feature/candle context, excluding identity and timestamp."""
    features = {
        field: _jsonable(alert[field])
        for field in _FINGERPRINT_FIELDS
        if field in alert
    }
    encoded = json.dumps(features, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _append(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        _jsonable(record),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    with _LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")
        handle.flush()


def record_manual_decision(
    *,
    session_id: str,
    candle_index: int,
    alert: Mapping[str, Any],
    decision: str,
    comment: str | None,
    score_model: str,
    blend_weight: float,
    cases_path: Path = DEFAULT_CASES_PATH,
    owner_decisions_path: Path = DEFAULT_OWNER_DECISIONS_PATH,
) -> dict[str, Any]:
    """Persist the complete feature context for an owner take or skip."""
    alert_id = str(alert.get("alert_id", alert.get("alertId", "")))
    if not alert_id:
        raise ValueError("A manual decision requires an alert id.")
    normalized_decision = decision.lower()
    if normalized_decision not in {"take", "skip"}:
        raise ValueError("A manual decision must be take or skip.")
    case_id = hashlib.sha256(f"{session_id}:{alert_id}".encode("utf-8")).hexdigest()
    normalized_comment = " ".join(str(comment or "").split())[:500] or None
    context = _jsonable(alert)
    record = {
        "eventType": "manual_decision",
        "caseId": case_id,
        "sessionId": session_id,
        "alertId": alert_id,
        "candleIndex": candle_index,
        "source": "manual",
        "decision": normalized_decision,
        "comment": normalized_comment,
        "formationFingerprint": formation_fingerprint(alert),
        "featureContext": context,
        "scoreModel": score_model,
        "blendWeight": blend_weight,
    }
    _append(owner_decisions_path, record)
    _append(cases_path, record)
    return record


def record_manual_outcome(
    *,
    case_id: str,
    trade: Mapping[str, Any],
    cases_path: Path = DEFAULT_CASES_PATH,
) -> dict[str, Any]:
    outcome = str(trade.get("outcome", "")).upper()
    if outcome not in {"WIN", "LOSS", "BREAKEVEN"}:
        raise ValueError("A resolved manual trade must have a terminal outcome.")
    record = {
        "eventType": "manual_outcome",
        "caseId": case_id,
        "source": "manual",
        "tradeId": trade.get("tradeId"),
        "outcome": outcome,
        "realizedR": trade.get("realizedR"),
        "netR": trade.get("netR"),
        "netPnl": trade.get("netPnl"),
    }
    _append(cases_path, record)
    return record


def read_manual_decisions(
    path: Path = DEFAULT_OWNER_DECISIONS_PATH,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    decisions: list[dict[str, Any]] = []
    with _LOCK, path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Owner decision ledger has invalid JSON on line {line_number}."
                ) from exc
            if (
                isinstance(record, dict)
                and record.get("eventType") == "manual_decision"
                and record.get("source") == "manual"
            ):
                decisions.append(record)
    return decisions


def read_manual_cases(
    path: Path = DEFAULT_CASES_PATH,
) -> list[dict[str, Any]]:
    """Join append-only manual decisions with their later outcome events."""
    if not path.exists():
        return []
    decisions: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    with _LOCK, path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"SI case ledger has invalid JSON on line {line_number}."
                ) from exc
            if not isinstance(record, dict):
                continue
            case_id = str(record.get("caseId", ""))
            if record.get("eventType") == "manual_decision":
                decisions[case_id] = record
            elif record.get("eventType") == "manual_outcome":
                outcomes[case_id] = record
    return [
        {**decision, "tradeOutcome": outcomes.get(case_id)}
        for case_id, decision in decisions.items()
    ]

