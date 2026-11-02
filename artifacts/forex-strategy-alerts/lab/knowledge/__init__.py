"""Append-only evidence admission for reusable local research knowledge."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

KNOWLEDGE_TYPES = frozenset(
    {"rule", "weights", "calibration", "fade-map", "owner-weight", "rejection-lesson"}
)
DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent
MIN_ADMISSION_SAMPLES = 30
_LOCK = threading.RLock()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Knowledge ledger line {line_number} is not an object.")
            rows.append(value)
    return rows


def admit_candidate(
    kind: str,
    candidate: Mapping[str, Any],
    *,
    sample_count: int,
    expectancy: float,
    baseline_expectancy: float,
    provenance: Mapping[str, Any],
    fresh: bool = True,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
) -> dict[str, Any]:
    """Admit only fresh, sourced evidence with n>=30 and positive lift."""
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
    if not isinstance(provenance, Mapping) or not provenance:
        raise ValueError("Knowledge provenance must be a non-empty object.")
    source = str(
        candidate.get("source", provenance.get("source", "evidence"))
    )
    if source not in {"evidence", "owner", "evolve", "manual"}:
        raise ValueError("Knowledge source must be evidence, owner, evolve, or manual.")
    candidate_payload = dict(candidate)
    candidate_key = hashlib.sha256(
        f"{kind}:{_canonical(candidate_payload)}".encode("utf-8")
    ).hexdigest()
    ledger_path = knowledge_dir / "ledger.jsonl"
    local_path = knowledge_dir / "local.jsonl"
    with _LOCK:
        ledger_rows = _read_jsonl(ledger_path)
        local_rows = _read_jsonl(local_path)
        if any(row.get("key") == candidate_key for row in ledger_rows):
            return {
                "status": "duplicate",
                "key": candidate_key,
                "reason": "This candidate has already been admitted.",
            }
        local_match = next(
            (row for row in local_rows if row.get("key") == candidate_key), None
        )
        reasons: list[str] = []
        if not fresh:
            reasons.append("stale evidence")
        if sample_count < MIN_ADMISSION_SAMPLES:
            reasons.append(f"sample count below {MIN_ADMISSION_SAMPLES}")
        if expectancy <= baseline_expectancy:
            reasons.append("expectancy does not exceed baseline")
        if reasons:
            status = "local"
        else:
            status = "admitted"
        learned_at = str(
            provenance.get("learned_at")
            or provenance.get("learnedAt")
            or datetime.now(timezone.utc).isoformat()
        )
        entry_provenance = {
            **dict(provenance),
            "n": sample_count,
            "expectancy": expectancy,
            "window": str(provenance.get("window", "unspecified")),
            "learned_at": learned_at,
        }
        entry = {
            "id": candidate_key,
            "schemaVersion": 1,
            "key": candidate_key,
            "kind": kind,
            "content": candidate_payload,
            "source": source,
            "candidate": candidate_payload,
            "sampleCount": sample_count,
            "expectancy": expectancy,
            "baselineExpectancy": baseline_expectancy,
            "provenance": entry_provenance,
            "version": 1,
            "status": status,
            "reasons": reasons,
            "recordedAtUtc": datetime.now(timezone.utc).isoformat(),
        }
        target = ledger_path if status == "admitted" else local_path
        if status == "local" and local_match is not None:
            return {
                "status": "local",
                "key": candidate_key,
                "reason": "; ".join(reasons),
                "entry": local_match,
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(entry) + "\n")
            handle.flush()
        return {
            "status": status,
            "key": candidate_key,
            "reason": "; ".join(reasons) if reasons else None,
            "entry": entry,
        }


def admitted_knowledge(
    kind: str | None = None,
    *,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
) -> list[dict[str, Any]]:
    rows = _read_jsonl(knowledge_dir / "ledger.jsonl")
    return [
        row for row in rows
        if row.get("status") == "admitted"
        and (kind is None or row.get("kind") == kind)
    ]


def top_admitted_knowledge(limit: int = 10) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("Knowledge limit must be a non-negative integer.")
    rows = admitted_knowledge()
    rows.sort(
        key=lambda row: (
            -float(row.get("expectancy", float("-inf"))),
            -int(row.get("sampleCount", 0)),
            str(row.get("kind", "")),
            str(row.get("key", "")),
        )
    )
    return rows[:limit]


def knowledge_summary(*, knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> dict[str, Any]:
    rows = admitted_knowledge(knowledge_dir=knowledge_dir)
    counts = {kind: 0 for kind in sorted(KNOWLEDGE_TYPES)}
    for row in rows:
        kind = str(row.get("kind"))
        if kind in counts:
            counts[kind] += 1
    return {
        "admittedCount": len(rows),
        "byType": counts,
        "keys": [str(row.get("key")) for row in rows],
        "highlights": [
            {
                "id": row.get("id", row.get("key")),
                "kind": row.get("kind"),
                "source": row.get("source"),
                "content": row.get("content", row.get("candidate")),
                "n": row.get("provenance", {}).get(
                    "n", row.get("sampleCount", 0)
                ),
                "expectancy": row.get("expectancy"),
                "window": row.get("provenance", {}).get("window"),
            }
            for row in sorted(
                rows,
                key=lambda item: (
                    -float(item.get("expectancy", float("-inf"))),
                    -int(item.get("sampleCount", 0)),
                    str(item.get("key", "")),
                ),
            )[:5]
        ],
    }

