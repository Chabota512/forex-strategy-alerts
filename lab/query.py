"""Local filter and summary CLI for alert feature snapshots."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .alert_snapshots import DEFAULT_SNAPSHOT_PATH, read_snapshots


def _wp_matches(snapshot_value: Any, query_value: str | None) -> bool:
    if query_value is None:
        return True
    value = str(snapshot_value or "").strip().lower()
    wanted = query_value.strip().lower()
    if wanted == "up":
        return value in {"up", "up-pressure"}
    if wanted == "down":
        return value in {"down", "down-pressure"}
    return value == wanted


def _decimal_field(row: Mapping[str, Any], key: str) -> Decimal:
    value = row.get(key)
    if value is None:
        raise ValueError(f"Resolved snapshot is missing {key}.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Resolved snapshot has invalid {key}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Resolved snapshot has non-finite {key}: {value!r}")
    return result


def _resolved_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    selected = list(rows)
    resolved = [
        row
        for row in selected
        if str(row.get("sim_outcome", "")).upper() in {"WIN", "LOSS"}
    ]
    count = len(resolved)
    wins = sum(str(row.get("sim_outcome", "")).upper() == "WIN" for row in resolved)
    total_r = sum(
        (_decimal_field(row, "r_multiple_achieved") for row in resolved),
        Decimal("0"),
    )
    total_net_r = sum(
        (_decimal_field(row, "sim_r_after_costs") for row in resolved),
        Decimal("0"),
    )
    total_pnl = sum(
        (_decimal_field(row, "sim_net_pnl") for row in resolved),
        Decimal("0"),
    )
    return {
        "alert_count": len(selected),
        "resolved_trade_count": count,
        "wins": wins,
        "win_rate_pct": Decimal(wins) * Decimal("100") / count if count else None,
        "average_r": total_r / count if count else None,
        "expectancy_after_costs": total_net_r / count if count else None,
        "total_pnl_after_costs": total_pnl,
    }


def crosstab_snapshots(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    shadow: str,
) -> dict[str, Any]:
    if shadow not in {"dc", "hier"}:
        raise ValueError("Crosstab shadow must be 'dc' or 'hier'.")
    shadow_field = "dc_state" if shadow == "dc" else "hier_state"
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    directional_pairs: list[tuple[str, str]] = []
    for row in snapshots:
        cst = str(row.get("cst", "")).strip()
        shadow_state = str(row.get(shadow_field, "")).strip()
        if not cst or not shadow_state:
            continue
        grouped[(cst, shadow_state)].append(row)
        if cst in {"uptrend", "downtrend"} and shadow_state in {
            "uptrend",
            "downtrend",
        }:
            directional_pairs.append((cst, shadow_state))

    agreement_count = sum(left == right for left, right in directional_pairs)
    comparison_count = len(directional_pairs)
    cells = []
    for (cst, shadow_state), rows in sorted(grouped.items()):
        cells.append(
            {
                "cst": cst,
                shadow: shadow_state,
                **_resolved_summary(rows),
            }
        )
    return {
        "shadow": shadow,
        "agreement_count": agreement_count,
        "directional_comparison_count": comparison_count,
        "agreement_pct": (
            Decimal(agreement_count) * Decimal("100") / comparison_count
            if comparison_count
            else None
        ),
        "cells": cells,
    }


def query_snapshots(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    variant: str | None = None,
    strength: str | None = None,
    wp: str | None = None,
    engulf: bool = False,
    direction: str | None = None,
    run_id: str | None = None,
    crosstab: str | None = None,
) -> dict[str, Any]:
    """Filter snapshots and calculate statistics for their resolved trades."""
    if crosstab not in {None, "cst,dc", "cst,hier"}:
        raise ValueError("Crosstab must be 'cst,dc' or 'cst,hier'.")
    matches = [
        snapshot
        for snapshot in snapshots
        if (variant is None or str(snapshot.get("variant", "")).upper() == variant)
        and (run_id is None or str(snapshot.get("run_id", "")) == run_id)
        and (
            strength is None
            or str(snapshot.get("strength", "")).upper() == strength
        )
        and _wp_matches(snapshot.get("wp_state"), wp)
        and (not engulf or snapshot.get("engulf") is True)
        and (
            direction is None
            or str(snapshot.get("direction", "")).upper() == direction
        )
    ]
    summary = _resolved_summary(matches)
    crosstab_result = (
        crosstab_snapshots(matches, shadow=crosstab.split(",", maxsplit=1)[1])
        if crosstab is not None
        else None
    )
    return {
        "matches": matches,
        "summary": summary,
        "crosstab": crosstab_result,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Filter local alert feature snapshots and summarize simulated results."
    )
    parser.add_argument(
        "--snapshots",
        type=Path,
        default=DEFAULT_SNAPSHOT_PATH,
        help=f"JSONL path (default: {DEFAULT_SNAPSHOT_PATH})",
    )
    parser.add_argument("--variant", choices=("A", "B"))
    parser.add_argument("--strength", choices=("STRONG", "NEUTRAL", "WEAK"))
    parser.add_argument(
        "--wp",
        choices=("up", "up-pressure", "balanced", "down", "down-pressure"),
        help="Wick pressure state.",
    )
    parser.add_argument("--engulf", action="store_true", help="Require engulfing alerts.")
    parser.add_argument("--direction", choices=("BUY", "SELL"))
    parser.add_argument(
        "--run-id",
        help="Limit results to one deterministic batch or replay run.",
    )
    parser.add_argument(
        "--crosstab",
        choices=("cst,dc", "cst,hier"),
        help="Compare frozen CST states with a shadow state.",
    )
    return parser


def _format_metric(value: Decimal | None, suffix: str = "") -> str:
    return f"{value:+.2f}{suffix}" if value is not None else "—"


def _format_money(value: Decimal) -> str:
    sign = "-" if value < 0 else "+"
    return f"{sign}${abs(value):.2f}"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        snapshots = read_snapshots(args.snapshots)
        result = query_snapshots(
            snapshots,
            variant=args.variant,
            strength=args.strength,
            wp=args.wp,
            engulf=args.engulf,
            direction=args.direction,
            run_id=args.run_id,
            crosstab=args.crosstab,
        )
    except (OSError, ValueError) as exc:
        print(f"Snapshot query failed: {exc}", file=sys.stderr)
        return 2

    summary = result["summary"]
    print(f"Matching alerts: {summary['alert_count']}")
    print(f"Resolved simulated trades: {summary['resolved_trade_count']}")
    win_rate = summary["win_rate_pct"]
    print(
        f"Win rate: {win_rate:.1f}% ({summary['wins']}/{summary['resolved_trade_count']})"
        if win_rate is not None
        else "Win rate: —"
    )
    print(f"Average R: {_format_metric(summary['average_r'])}")
    print(
        "Expectancy after costs: "
        f"{_format_metric(summary['expectancy_after_costs'], 'R')}"
    )
    print(
        "Total P&L after costs: "
        + (
            _format_money(summary["total_pnl_after_costs"])
            if summary["resolved_trade_count"]
            else "— (no resolved trades)"
        )
    )
    if result["crosstab"] is not None:
        table = result["crosstab"]
        agreement = table["agreement_pct"]
        shadow = table["shadow"]
        print(
            f"CST/{shadow.upper()} directional agreement: {agreement:.1f}% "
            f"({table['agreement_count']}/{table['directional_comparison_count']})"
            if agreement is not None
            else "CST/shadow directional agreement: —"
        )
        for cell in table["cells"]:
            print(
                f"CST={cell['cst']} {shadow.upper()}={cell[shadow]}: "
                f"{cell['alert_count']} alerts, "
                f"{cell['resolved_trade_count']} resolved, "
                f"win rate={cell['win_rate_pct'] if cell['win_rate_pct'] is not None else '—'}, "
                f"avg R={_format_metric(cell['average_r'])}, "
                f"expectancy={_format_metric(cell['expectancy_after_costs'], 'R')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
