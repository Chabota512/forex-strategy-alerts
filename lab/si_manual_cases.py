"""Append-only manual replay decision and outcome case base."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Mapping

DEFAULT_MANUAL_CASES_PATH = (
    Path(__file__).resolve().parent
    / "output"
    / "system-intelligence"
    / "owner-decisions.jsonl"
)
_LOCK = threading.RLock()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Manual case line {line_number} is not an object.")
            rows.append(value)
    return rows


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(_canonical(dict(row)) + "\n")


def fingerprint_formation(
    alert: Mapping[str, Any],
    formation: Mapping[str, Any] | None = None,
) -> str:
    """Hash the causal formation context used to score this alert."""
    basis: Any = formation if formation else {
        "symbol": alert.get("symbol"),
        "timeframe": alert.get("tf", alert.get("timeframe")),
        "time": alert.get("time"),
        "direction": alert.get("direction"),
        "candle": alert.get("candle"),
        "previousCandle": alert.get("previous_candle", alert.get("previousCandle")),
    }
    return hashlib.sha256(_canonical(basis).encode("utf-8")).hexdigest()


def record_manual_decision(
    path: Path,
    *,
    case_id: str,
    session_id: str,
    alert_id: str,
    candle_index: int,
    decision: str,
    comment: str | None,
    formation: Mapping[str, Any] | None,
    feature_context: Mapping[str, Any],
    score_model: str,
    blend_weight: float,
    trade_direction: str | None = None,
) -> dict[str, Any]:
    """Persist a take or skip decision with comments and full causal features."""
    if decision not in {"take", "skip"}:
        raise ValueError("Manual decisions must be take or skip.")
    row = {
        "eventType": "manual_decision",
        "caseId": case_id,
        "sessionId": session_id,
        "alertId": alert_id,
        "candleIndex": candle_index,
        "source": "manual",
        "decision": decision,
        "comment": comment.strip()[:500] if comment and comment.strip() else None,
        "formationFingerprint": fingerprint_formation(feature_context, formation),
        "featureContext": dict(feature_context),
        "scoreModel": score_model,
        "blendWeight": float(blend_weight),
    }
    if trade_direction is not None:
        row["tradeDirection"] = trade_direction
    # Return the same JSON-safe shape that is persisted. Replay alerts may
    # contain datetime values, which the API response encoder cannot serialize.
    row = json.loads(_canonical(row))
    with _LOCK:
        rows = _read_rows(path)
        for existing in rows:
            if (
                existing.get("eventType") == "manual_decision"
                and existing.get("caseId") == case_id
            ):
                if existing.get("decision") != decision:
                    raise ValueError("This manual case already has a different decision.")
                return existing
        _append(path, row)
    return row


def record_manual_outcome(
    path: Path,
    *,
    case_id: str,
    trade_id: str,
    net_r: float,
    outcome: str,
) -> dict[str, Any]:
    """Persist a resolved taken trade once, for owner-model performance tracking."""
    row = {
        "eventType": "manual_outcome",
        "caseId": case_id,
        "tradeId": trade_id,
        "netR": float(net_r),
        "outcome": str(outcome),
    }
    with _LOCK:
        rows = _read_rows(path)
        for existing in rows:
            if (
                existing.get("eventType") == "manual_outcome"
                and existing.get("caseId") == case_id
                and existing.get("tradeId") == trade_id
            ):
                return existing
        _append(path, row)
    return row


def read_manual_decisions(path: Path = DEFAULT_MANUAL_CASES_PATH) -> list[dict[str, Any]]:
    return [
        row
        for row in _read_rows(path)
        if row.get("eventType") == "manual_decision"
        and row.get("source") == "manual"
        and row.get("decision") in {"take", "skip"}
    ]


def read_manual_cases(path: Path = DEFAULT_MANUAL_CASES_PATH) -> list[dict[str, Any]]:
    rows = _read_rows(path)
    outcomes: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("eventType") == "manual_outcome":
            outcomes[str(row.get("caseId", ""))] = row
    cases = []
    for row in rows:
        if row.get("eventType") != "manual_decision":
            continue
        case = dict(row)
        outcome = outcomes.get(str(row.get("caseId", "")))
        if outcome is not None:
            case["tradeOutcome"] = {
                "tradeId": outcome.get("tradeId"),
                "netR": outcome.get("netR"),
                "outcome": outcome.get("outcome"),
            }
        cases.append(case)
    return cases
