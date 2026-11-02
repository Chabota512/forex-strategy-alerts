"""Append-only admission gate for reusable, evidence-backed local knowledge."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..si_cases import DEFAULT_STATE_PATH

KNOWLEDGE_TYPES = frozenset(
    {"rule", "weights", "calibration", "fade-map", "owner-weight", "rejection-lesson"}
)
MIN_ADMISSION_SAMPLES = 30
DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent
_LOCK = threading.RLock()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Knowledge ledger line {line_number} is not an object.")
            rows.append(value)
    return rows


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(_canonical(dict(row)) + "\n")


def _lesson_is_recorded(
    candidate: Mapping[str, Any],
    lesson_ledger: Sequence[Mapping[str, Any]],
) -> bool:
    lesson_id = candidate.get("lessonId")
    return bool(lesson_id) and any(
        str(row.get("lesson_id", row.get("lessonId", ""))) == str(lesson_id)
        for row in lesson_ledger
    )


def _read_owner_lesson_ledger() -> list[dict[str, Any]]:
    if not DEFAULT_STATE_PATH.exists():
        return []
    state = json.loads(DEFAULT_STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(state, Mapping):
        raise ValueError("Owner lesson ledger state must be an object.")
    lessons = state.get("lessons", [])
    if not isinstance(lessons, list):
        raise ValueError("Owner lesson ledger must contain a list of lessons.")
    return [row for row in lessons if isinstance(row, dict)]


def admit_candidate(
    kind: str,
    candidate: Mapping[str, Any],
    *,
    sample_count: int,
    expectancy: float,
    baseline_expectancy: float,
    provenance: Mapping[str, Any],
    fresh: bool = True,
    lesson_ledger: Sequence[Mapping[str, Any]] | None = None,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
) -> dict[str, Any]:
    """Write as admitted only when evidence, freshness, provenance, and lesson rules pass."""
    if kind not in KNOWLEDGE_TYPES:
        raise ValueError(f"Unsupported knowledge type: {kind}")
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < 0
    ):
        raise ValueError("sample_count must be a non-negative integer.")
    if (
        isinstance(expectancy, bool)
        or isinstance(baseline_expectancy, bool)
        or not math.isfinite(float(expectancy))
        or not math.isfinite(float(baseline_expectancy))
    ):
        raise ValueError("Expectancy values must be finite numbers.")
    if not isinstance(fresh, bool):
        raise ValueError("fresh must be a boolean.")
    if not isinstance(candidate, Mapping) or not candidate:
        raise ValueError("A knowledge candidate must be a non-empty object.")
    if (
        not isinstance(provenance, Mapping)
        or not str(provenance.get("source", "")).strip()
    ):
        raise ValueError("Knowledge provenance must include a non-empty source.")

    payload = dict(candidate)
    source = str(payload.get("source", provenance.get("source", "")))
    if source not in {"evidence", "owner", "evolve", "manual"}:
        raise ValueError("Knowledge source must be evidence, owner, evolve, or manual.")
    key = hashlib.sha256(
        f"{kind}:{_canonical(payload)}".encode("utf-8")
    ).hexdigest()
    reasons: list[str] = []
    if sample_count < MIN_ADMISSION_SAMPLES:
        reasons.append("sample_floor")
    if float(expectancy) <= float(baseline_expectancy):
        reasons.append("expectancy_not_above_baseline")
    if not fresh:
        reasons.append("stale")
    if kind == "rejection-lesson":
        active_lesson_ledger = (
            list(lesson_ledger)
            if lesson_ledger is not None
            else _read_owner_lesson_ledger()
        )
        if not _lesson_is_recorded(payload, active_lesson_ledger):
            reasons.append("lesson_not_in_ledger")

    row = {
        "id": key,
        "key": key,
        "kind": kind,
        "candidate": payload,
        "source": source,
        "sampleCount": sample_count,
        "expectancy": float(expectancy),
        "baselineExpectancy": float(baseline_expectancy),
        "provenance": dict(provenance),
        "fresh": fresh,
        "status": "local" if reasons else "admitted",
        "reasons": reasons,
        "recordedAtUtc": datetime.now(timezone.utc).isoformat(),
    }
    target = knowledge_dir / ("local.jsonl" if reasons else "ledger.jsonl")
    with _LOCK:
        ledger_rows = _read_jsonl(knowledge_dir / "ledger.jsonl")
        local_rows = _read_jsonl(knowledge_dir / "local.jsonl")
        if any(existing.get("key") == key for existing in ledger_rows + local_rows):
            existing = next(
                existing
                for existing in ledger_rows + local_rows
                if existing.get("key") == key
            )
            return {"status": "duplicate", "key": key, "entry": existing}
        _append(target, row)
    return {"status": row["status"], "key": key, "entry": row}


def admitted_knowledge(
    kind: str | None = None,
    *,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _read_jsonl(knowledge_dir / "ledger.jsonl")
        if row.get("status") == "admitted"
    ]
    if kind is not None:
        rows = [row for row in rows if row.get("kind") == kind]
    return rows


def top_admitted_knowledge(
    *,
    limit: int = 5,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer.")
    rows = admitted_knowledge(knowledge_dir=knowledge_dir)
    return sorted(
        rows,
        key=lambda row: (
            -float(row.get("expectancy", float("-inf"))),
            -int(row.get("sampleCount", 0)),
            str(row.get("key", "")),
        ),
    )[:limit]
