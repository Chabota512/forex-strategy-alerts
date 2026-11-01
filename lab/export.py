"""Export any Strategy Lab spec as a human-readable text file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if __package__:
    from .backtest import DEFAULT_SPEC, load_spec, settings_fingerprint
else:
    from backtest import DEFAULT_SPEC, load_spec, settings_fingerprint


def render_spec_text(spec: dict[str, Any]) -> str:
    reward = spec["reward"]
    validation = reward["validation"]
    floors = reward["floors"]
    lines = [
        "FOREX STRATEGY LAB — SPECIFICATION EXPORT",
        "=" * 48,
        f"Name: {spec.get('name', 'Unnamed strategy')}",
        f"Version: {spec['version']}",
        f"Status: {spec.get('status', 'unspecified')}",
        f"Frozen: {'yes' if spec.get('frozen', False) else 'no'}",
        f"Settings fingerprint: {settings_fingerprint(spec)}",
        f"Source: {spec.get('source', 'not specified')}",
        "",
        "MARKETS",
        f"Instruments: {', '.join(spec['instruments'])}",
        f"Timeframes: {', '.join(spec['timeframes'])}",
        "",
        "TRIGGER",
        f"Evaluation: {spec['trigger'].get('evaluate', 'not specified')}",
        "SELL:",
        *[f"  - {rule}" for rule in spec["trigger"].get("sell", [])],
        "BUY:",
        *[f"  - {rule}" for rule in spec["trigger"].get("buy", [])],
        f"Both extremes: {spec['trigger'].get('both_extremes_broken', 'not specified')}",
        "",
        "CCT — DESCRIPTIVE CONTEXT",
        f"Window: {spec['cct']['window']} candles",
        f"Swing neighbors: {spec['cct']['swing_neighbors']}",
        f"Role: {spec['cct'].get('role', 'descriptive only')}",
        "",
        "CST — STRUCTURAL CONTEXT",
        f"Major swing neighbors: {spec['cst']['major_swing_neighbors']}",
        f"Break basis: {spec['cst']['break_basis']}",
        "Strength labels: STRONG / NEUTRAL / WEAK; labels never suppress alerts.",
        "",
        "REWARD AND VALIDATION",
        f"Objective: {reward['objective']}",
        f"Lookahead: {reward['lookahead_candles']} candles after the alert candle",
        f"Same-candle tie: {reward['same_candle_tie']}",
        f"Speed bonus within: {reward['speed_bonus']['within_candles']} candles",
        "Thresholds:",
    ]
    for symbol in spec["instruments"]:
        threshold = reward["thresholds"][symbol]
        lines.append(
            f"  - {symbol}: D_min={threshold['d_min']} / A_max={threshold['a_max']} "
            f"({threshold.get('unit', 'price units')})"
        )
    lines.extend(
        [
            f"Training/validation split: {validation['training_months']}/"
            f"{validation['validation_months']} of {validation['total_months']} months",
            "Validation floors:",
            f"  - At least {floors['minimum_validation_alerts_per_symbol_timeframe']} "
            "alerts per symbol/timeframe",
            f"  - At least {floors['minimum_months_with_alerts']} of "
            f"{floors['months_in_coverage_period']} months with alerts",
            f"Challenger improvement required: "
            f"{reward['challenger']['minimum_validation_score_improvement']}",
            "",
            "SAFETY",
            "Alerts are informational analysis only.",
            "No orders, auto-trading, profit claims, or probability claims.",
            "The seed spec is preserved separately and is not modified by export.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a versioned strategy spec as readable text."
    )
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output", type=Path, help="Write to a .txt file instead of stdout.")
    args = parser.parse_args(argv)

    try:
        spec = load_spec(args.spec)
        text = render_spec_text(spec)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
            print(f"Text export written to {args.output}")
        else:
            sys.stdout.write(text)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Export stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
