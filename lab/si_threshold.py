"""Windowed, evidence-backed calibration for the XAUUSD SI score floor."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

AUTO_SI_MINIMUM_HISTORY = 200
AUTO_SI_REQUIRED_CASES = AUTO_SI_MINIMUM_HISTORY + 1
AUTO_SI_WINDOW_SIZE = 500
AUTO_SI_MAX_THRESHOLD = 0.8
AUTO_SI_GRID_STEP = 0.01
AUTO_SI_MIN_RETENTION = 0.4


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def calibrate_min_si_score(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pick the best SI floor on the latest 500 resolved real XAUUSD cases."""
    scored: list[tuple[int, str, float, float]] = []
    for index, case in enumerate(cases):
        if str(case.get("symbol", "")).upper() != "XAUUSD":
            continue
        if str(case.get("case_type", "real")).lower() != "real":
            continue
        outcome = case.get("outcome")
        if outcome is not None and str(outcome).upper() not in {"WIN", "LOSS"}:
            continue
        features = case.get("features")
        feature_values = features if isinstance(features, Mapping) else {}
        score = _finite_number(
            case.get(
                "si_score",
                case.get(
                    "siScore",
                    feature_values.get("si_score", feature_values.get("siScore")),
                ),
            )
        )
        net_r = _finite_number(case.get("net_r", case.get("netR")))
        if score is None or not 0 <= score <= 1 or net_r is None:
            continue
        resolved_at = str(
            case.get("resolved_at_utc")
            or case.get("exit_time")
            or case.get("entry_time")
            or ""
        )
        scored.append((index, resolved_at, score, net_r))

    # Case history is normally append-ordered. Sorting by resolution time also
    # keeps imported batches and replay windows deterministic.
    scored.sort(key=lambda item: (item[1], item[0]))
    window = scored[-AUTO_SI_WINDOW_SIZE:]
    count = len(window)
    base: dict[str, Any] = {
        "status": "calibrating" if count < AUTO_SI_REQUIRED_CASES else "calibrated",
        "caseCount": count,
        "totalScoredCases": len(scored),
        "requiredCaseCount": AUTO_SI_REQUIRED_CASES,
        "casesNeeded": max(0, AUTO_SI_REQUIRED_CASES - count),
        "windowSize": AUTO_SI_WINDOW_SIZE,
        "minimumRetention": AUTO_SI_MIN_RETENTION,
        "gridStep": AUTO_SI_GRID_STEP,
        "maxThreshold": AUTO_SI_MAX_THRESHOLD,
        "windowStartUtc": window[0][1] or None if window else None,
        "windowEndUtc": window[-1][1] or None if window else None,
    }
    if count < AUTO_SI_REQUIRED_CASES:
        return {
            **base,
            "threshold": 0.0,
            "filteredCaseCount": count,
            "retention": 1.0 if count else None,
            "baselineExpectancyR": (
                sum(item[3] for item in window) / count if count else None
            ),
            "filteredExpectancyR": (
                sum(item[3] for item in window) / count if count else None
            ),
            "message": f"Calibrating after {AUTO_SI_REQUIRED_CASES - count} more cases",
        }

    best: tuple[float, float, int, float] | None = None
    step_count = round(AUTO_SI_MAX_THRESHOLD / AUTO_SI_GRID_STEP)
    for step in range(step_count + 1):
        threshold = round(step * AUTO_SI_GRID_STEP, 2)
        retained = [item for item in window if item[2] >= threshold]
        retention = len(retained) / count
        if retention + 1e-12 < AUTO_SI_MIN_RETENTION:
            continue
        expectancy = sum(item[3] for item in retained) / len(retained)
        # Iteration is ascending, so exact expectancy ties retain the lower
        # threshold and the larger sample.
        if best is None or expectancy > best[0] + 1e-12:
            best = (expectancy, threshold, len(retained), retention)

    if best is None:
        # A zero threshold always retains all validated [0, 1] SI scores.
        raise RuntimeError("Auto SI MIN could not find a threshold candidate.")
    expectancy, threshold, retained_count, retention = best
    return {
        **base,
        "threshold": threshold,
        "filteredCaseCount": retained_count,
        "retention": retention,
        "baselineExpectancyR": sum(item[3] for item in window) / count,
        "filteredExpectancyR": expectancy,
        "message": "Calibrated from scored case history",
    }
