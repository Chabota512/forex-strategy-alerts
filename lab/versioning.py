"""Safe, file-based candidate and champion version management."""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .backtest import LAB_DIR, load_spec
else:
    from backtest import LAB_DIR, load_spec

DEFAULT_SEED = LAB_DIR / "specs" / "seed_v1.5.0.json"
DEFAULT_CHAMPION = LAB_DIR / "specs" / "champion.json"
DEFAULT_PROPOSALS = LAB_DIR / "specs" / "proposals"
DEFAULT_VERSIONS = LAB_DIR / "versions"
CHANGELOG = DEFAULT_VERSIONS / "changelog.md"
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
MAX_CHANGES = 6


class VersioningError(ValueError):
    """Raised when a candidate or promotion violates versioning rules."""


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def next_patch_version(version: str) -> str:
    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        raise VersioningError(f"Spec version must use MAJOR.MINOR.PATCH: {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_json_spec(path: Path) -> dict[str, Any]:
    return load_spec(path)


def get_nested(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise VersioningError(f"Unknown spec path: {path}")
        current = current[segment]
    return current


def set_nested(value: dict[str, Any], path: str, new_value: Any) -> None:
    parts = path.split(".")
    current: Any = value
    for segment in parts[:-1]:
        if not isinstance(current, dict) or segment not in current:
            raise VersioningError(f"Unknown spec path: {path}")
        current = current[segment]
    if not isinstance(current, dict) or parts[-1] not in current:
        raise VersioningError(f"Unknown spec path: {path}")
    current[parts[-1]] = new_value


def _allowed_change(path: str, spec: dict[str, Any]) -> bool:
    fixed_paths = {
        "cct.window",
        "cct.swing_neighbors",
        "cst.major_swing_neighbors",
        "cst.break_basis",
    }
    if path in fixed_paths:
        return True
    parts = path.split(".")
    return (
        len(parts) == 4
        and parts[0:2] == ["reward", "thresholds"]
        and parts[2] in spec["instruments"]
        and parts[3] in {"d_min", "a_max"}
    )


def _validate_change_value(path: str, value: Any) -> Any:
    if path in {"cct.window", "cct.swing_neighbors", "cst.major_swing_neighbors"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise VersioningError(f"{path} must be an integer")
        limits = {
            "cct.window": (5, 60),
            "cct.swing_neighbors": (1, 8),
            "cst.major_swing_neighbors": (2, 20),
        }
        minimum, maximum = limits[path]
        if not minimum <= value <= maximum:
            raise VersioningError(
                f"{path} must be between {minimum} and {maximum}"
            )
        return value
    if path == "cst.break_basis":
        if value not in {"close", "wick"}:
            raise VersioningError("cst.break_basis must be 'close' or 'wick'")
        return value
    try:
        from decimal import Decimal

        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise VersioningError(f"{path} must be a positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise VersioningError(f"{path} must be a positive finite decimal")
    return format(parsed.normalize(), "f")


def build_candidate(
    base_spec: dict[str, Any],
    *,
    changes: list[dict[str, Any]],
    summary: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if base_spec.get("frozen") is True:
        raise VersioningError("The selected baseline is frozen and cannot be evolved.")
    if not changes or len(changes) > MAX_CHANGES:
        raise VersioningError(f"A proposal must contain 1 to {MAX_CHANGES} changes.")

    candidate = copy.deepcopy(base_spec)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in changes:
        if not isinstance(item, dict) or set(item) != {"path", "value"}:
            raise VersioningError("Each change must contain only 'path' and 'value'.")
        path = item["path"]
        if not isinstance(path, str) or not _allowed_change(path, base_spec):
            raise VersioningError(f"Change path is not allowed: {path!r}")
        if path in seen:
            raise VersioningError(f"Duplicate change path: {path}")
        seen.add(path)
        current_value = get_nested(candidate, path)
        value = _validate_change_value(path, item["value"])
        if str(current_value) == str(value):
            raise VersioningError(f"Proposed value for {path} does not change it.")
        set_nested(candidate, path, value)
        normalized.append({"path": path, "old_value": current_value, "value": value})

    cct_window = int(candidate["cct"]["window"])
    cct_neighbors = int(candidate["cct"]["swing_neighbors"])
    if cct_window < (2 * cct_neighbors + 3):
        raise VersioningError(
            "cct.window must be at least 2 * cct.swing_neighbors + 3."
        )
    for symbol, threshold in candidate["reward"]["thresholds"].items():
        if threshold["d_min"] == threshold["a_max"]:
            continue
        try:
            from decimal import Decimal

            d_min = Decimal(str(threshold["d_min"]))
            a_max = Decimal(str(threshold["a_max"]))
        except (ArithmeticError, ValueError) as exc:
            raise VersioningError(f"Invalid reward threshold for {symbol}") from exc
        if d_min <= 0 or a_max <= 0:
            raise VersioningError(f"Reward thresholds for {symbol} must be positive.")

    candidate["version"] = next_patch_version(str(base_spec["version"]))
    candidate["status"] = "candidate_pending_review"
    candidate["frozen"] = False
    candidate["candidate_summary"] = summary[:1000]
    candidate["candidate_changes"] = normalized
    return candidate, normalized


def candidate_filename(version: str, *, timestamp: str | None = None) -> str:
    safe_version = version.replace(".", "_")
    return f"candidate_v{safe_version}_{timestamp or utc_stamp()}.json"


def write_candidate(
    candidate: dict[str, Any],
    *,
    proposals_dir: Path = DEFAULT_PROPOSALS,
) -> Path:
    path = proposals_dir / candidate_filename(str(candidate["version"]))
    atomic_write_json(path, candidate)
    load_spec(path)
    return path


def update_candidate_review(
    candidate_path: Path,
    *,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    if decision not in {"rejected", "promoted", "not_eligible"}:
        raise VersioningError("Invalid candidate review decision.")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["review"] = {
        "decision": decision,
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": reason[:1000],
    }
    atomic_write_json(candidate_path, candidate)
    return candidate


def archive_spec(
    spec: dict[str, Any],
    *,
    reason: str,
    versions_dir: Path = DEFAULT_VERSIONS,
    timestamp: str | None = None,
) -> Path:
    versions_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{timestamp or utc_stamp()}_v{str(spec['version']).replace('.', '_')}"
    path = versions_dir / f"{stem}.json"
    suffix = 1
    while path.exists():
        path = versions_dir / f"{stem}_{suffix}.json"
        suffix += 1
    archived = copy.deepcopy(spec)
    archived["archive"] = {
        "archived_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": reason,
    }
    atomic_write_json(path, archived)
    return path


def append_changelog(
    *,
    previous_version: str,
    new_version: str,
    summary: str,
    validation_score: str,
    improvement_percent: str,
    floors_status: str,
    archive_path: Path,
    changelog_path: Path = CHANGELOG,
) -> None:
    changelog_path.parent.mkdir(parents=True, exist_ok=True)
    if not changelog_path.exists():
        changelog_path.write_text("# Strategy version changelog\n\n", encoding="utf-8")
    entry = (
        f"## {new_version} — {datetime.now(timezone.utc).date().isoformat()}\n\n"
        f"- Previous baseline: `{previous_version}`\n"
        f"- Validation reward: `{validation_score}`\n"
        f"- Improvement vs baseline: `{improvement_percent}%`\n"
        f"- Validation floors: **{floors_status}**\n"
        f"- Summary: {summary.strip() or 'Owner-approved candidate'}\n"
        f"- Archived previous spec: `{archive_path.name}`\n"
        "- Promotion: manually approved in the terminal; seed remains unchanged and unfrozen.\n\n"
    )
    with changelog_path.open("a", encoding="utf-8") as stream:
        stream.write(entry)


def promote_candidate(
    candidate: dict[str, Any],
    *,
    baseline: dict[str, Any],
    champion_path: Path = DEFAULT_CHAMPION,
    versions_dir: Path = DEFAULT_VERSIONS,
    changelog_path: Path = CHANGELOG,
    summary: str,
    validation_score: str,
    improvement_percent: str,
    floors_status: str,
) -> Path:
    if candidate.get("status") != "candidate_pending_review":
        raise VersioningError("Only a pending candidate can be promoted.")
    if candidate.get("frozen") is True:
        raise VersioningError("A frozen candidate cannot be promoted.")
    archive_path = archive_spec(
        baseline,
        reason="Baseline archived before manually approved promotion.",
        versions_dir=versions_dir,
    )
    promoted = copy.deepcopy(candidate)
    promoted["status"] = "owner_approved_unfrozen"
    promoted["frozen"] = False
    promoted.pop("candidate_summary", None)
    promoted.pop("candidate_changes", None)
    promoted["promotion"] = {
        "approved_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "previous_version": str(baseline["version"]),
        "approval": "manual_owner_terminal_prompt",
    }
    atomic_write_json(champion_path, promoted)
    load_spec(champion_path)
    append_changelog(
        previous_version=str(baseline["version"]),
        new_version=str(promoted["version"]),
        summary=summary,
        validation_score=validation_score,
        improvement_percent=improvement_percent,
        floors_status=floors_status,
        archive_path=archive_path,
        changelog_path=changelog_path,
    )
    return archive_path
