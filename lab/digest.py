"""Compact local-only snapshots digest for later analysis consumers."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .alert_snapshots import DEFAULT_SNAPSHOT_PATH, read_snapshots

MIN_CELL_SIZE = 30
MAX_DIGEST_TOKENS = 2_000
_FEATURE_AXES = (
    "variant",
    "tf",
    "period",
    "direction",
    "strength",
    "wp_state",
    "engulf",
    "cst",
    "cst_dc",
    "cst_hier",
    "mtf_cst",
    "mtf_cct",
    "mtf_wick_pressure",
    "mtf_trend_agree",
    "mtf_cct_agree",
    "mtf_wp_agree",
    "dual_tf_alert",
)
_MTF_AGREEMENT_AXES = frozenset(
    {"mtf_trend_agree", "mtf_wp_agree", "dual_tf_alert", "mtf_cct_agree"}
)


def _decimal(row: Mapping[str, Any], field: str) -> Decimal:
    value = row.get(field)
    if value is None:
        raise ValueError(f"Resolved snapshot is missing {field}.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Resolved snapshot has invalid {field}: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"Resolved snapshot has non-finite {field}: {value!r}")
    return parsed


def _cell_metrics(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    resolved = [
        row
        for row in rows
        if str(row.get("sim_outcome", "")).upper() in {"WIN", "LOSS"}
    ]
    count = len(resolved)
    wins = sum(str(row.get("sim_outcome", "")).upper() == "WIN" for row in resolved)
    average_r = (
        sum((_decimal(row, "r_multiple_achieved") for row in resolved), Decimal("0"))
        / count
        if count
        else None
    )
    expectancy = (
        sum((_decimal(row, "sim_r_after_costs") for row in resolved), Decimal("0"))
        / count
        if count
        else None
    )
    return {
        "n": len(rows),
        "resolved": count,
        "wins": wins,
        "win_rate_pct": (
            round(float(Decimal(wins) * Decimal("100") / count), 2)
            if count
            else None
        ),
        "avg_r": round(float(average_r), 4) if average_r is not None else None,
        "expectancy_after_costs": (
            round(float(expectancy), 4) if expectancy is not None else None
        ),
    }


def _eligible_value(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized if normalized else None


def _groups_for_axis(
    rows: list[Mapping[str, Any]],
    axis: str,
    min_cell_size: int,
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if axis == "cst_dc":
            left = _eligible_value(row.get("cst"))
            right = _eligible_value(row.get("dc_state"))
            value = f"{left}|{right}" if left and right else None
        elif axis == "cst_hier":
            left = _eligible_value(row.get("cst"))
            right = _eligible_value(row.get("hier_state"))
            value = f"{left}|{right}" if left and right else None
        else:
            value = _eligible_value(row.get(axis))
        if value is not None:
            groups[value].append(row)
    eligible = [
        {"axis": axis, "value": value, **_cell_metrics(members)}
        for value, members in groups.items()
        if len(members) >= min_cell_size
    ]
    return sorted(eligible, key=lambda item: (-item["n"], item["axis"], item["value"]))


def _token_estimate(value: Mapping[str, Any]) -> int:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    # Count words and punctuation separately; this intentionally overestimates
    # normal tokenizer counts for compact JSON.
    return len(re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9\s]", encoded))


def _is_protected_agreement_cell(cell: Mapping[str, Any]) -> bool:
    return (
        cell.get("axis") in _MTF_AGREEMENT_AXES
        and str(cell.get("value", "")).strip().lower() == "true"
    )


def build_digest(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    min_cell_size: int = MIN_CELL_SIZE,
    run_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    period_label: str | None = None,
    max_tokens: int = MAX_DIGEST_TOKENS,
) -> dict[str, Any]:
    if min_cell_size < 1:
        raise ValueError("Minimum cell size must be at least one.")
    if max_tokens < 100:
        raise ValueError("Digest token budget must be at least 100.")
    if (from_date is None) != (to_date is None):
        raise ValueError("Both from_date and to_date are required together.")
    if from_date and from_date > to_date:
        raise ValueError("from_date must be on or before to_date.")

    rows = [
        row
        for row in snapshots
        if (run_id is None or str(row.get("run_id", "")) == run_id)
        and (
            from_date is None
            or from_date <= str(row.get("time", ""))[:10] <= str(to_date)
        )
    ]
    groups = [
        cell
        for axis in _FEATURE_AXES
        for cell in _groups_for_axis(rows, axis, min_cell_size)
    ]
    groups.sort(key=lambda item: (-item["n"], item["axis"], item["value"]))

    digest: dict[str, Any] = {
        "schema_version": 1,
        "period": period_label or (
            f"{from_date}..{to_date}" if from_date is not None else "all selected rows"
        ),
        "snapshot_count": len(rows),
        "sample_floor": min_cell_size,
        "groups": groups,
        "truncated_cells": 0,
        "estimated_tokens": 0,
    }
    digest["estimated_tokens"] = _token_estimate(digest)
    while digest["estimated_tokens"] > max_tokens and digest["groups"]:
        discard_index = next(
            (
                index
                for index in range(len(digest["groups"]) - 1, -1, -1)
                if not _is_protected_agreement_cell(digest["groups"][index])
            ),
            None,
        )
        if discard_index is None:
            break
        digest["groups"].pop(discard_index)
        digest["truncated_cells"] += 1
        digest["estimated_tokens"] = _token_estimate(digest)
    protected_groups_remain = any(
        _is_protected_agreement_cell(group) for group in digest["groups"]
    )
    if digest["estimated_tokens"] > max_tokens and not protected_groups_remain:
        raise ValueError("Digest metadata exceeds the configured token budget.")
    return digest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize local alert snapshots as compact JSON."
    )
    parser.add_argument("--snapshots", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument("--run-id")
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--period-label")
    parser.add_argument("--min-cell-size", type=int, default=MIN_CELL_SIZE)
    parser.add_argument("--max-tokens", type=int, default=MAX_DIGEST_TOKENS)
    args = parser.parse_args(argv)
    try:
        digest = build_digest(
            read_snapshots(args.snapshots),
            run_id=args.run_id,
            from_date=args.from_date,
            to_date=args.to_date,
            period_label=args.period_label,
            min_cell_size=args.min_cell_size,
            max_tokens=args.max_tokens,
        )
    except (OSError, ValueError) as exc:
        print(f"Digest stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(digest, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
