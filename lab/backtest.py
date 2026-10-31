"""Run the versioned candle strategy over local CSV data only."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

if __package__:
    from .candle_data import (
        DEFAULT_DATA_DIR,
        LAB_DIR,
        CandleFileData,
        load_candle_file,
        load_candles_with_metadata,
        sha256_file,
    )
    from .data_sanity import (
        require_owner_approved_derived_timeframe,
        require_owner_approved_sanity,
    )
    from .engine import (
        Candle,
        CandleDataError,
        body_engulfs_previous,
        calculate_strength,
        classify_cct,
        compute_cst,
        detect_trigger,
        detect_trigger_b,
        score_alert,
        smaller_body_note,
        strength_for,
    )
    from .shadow_detectors import compute_shadow_states
else:
    from candle_data import (
        DEFAULT_DATA_DIR,
        LAB_DIR,
        CandleFileData,
        load_candle_file,
        load_candles_with_metadata,
        sha256_file,
    )
    from data_sanity import (
        require_owner_approved_derived_timeframe,
        require_owner_approved_sanity,
    )
    from engine import (
        Candle,
        CandleDataError,
        body_engulfs_previous,
        calculate_strength,
        classify_cct,
        compute_cst,
        detect_trigger,
        detect_trigger_b,
        score_alert,
        smaller_body_note,
        strength_for,
    )
    from shadow_detectors import compute_shadow_states

DEFAULT_SPEC = LAB_DIR / "specs" / "seed_v1.5.0.json"
DEFAULT_OUTPUT = LAB_DIR / "output" / "alerts.csv.gz"
DEFAULT_REPORT = LAB_DIR / "output" / "report.json"
DEFAULT_SMOKE_OUTPUT_DIR = LAB_DIR / "output" / "smoke"
ALERT_FIELDS = (
    "time",
    "symbol",
    "tf",
    "direction",
    "cct",
    "cst",
    "strength_label",
    "note",
    "period",
    "reward_score",
    "reward_status",
    "spec_version",
    "spec_status",
    "settings_fingerprint",
    "cst_settings_json",
    "reward_settings_json",
    "input_source",
    "input_sha256",
    "run_id",
    "alert_type",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def effective_settings(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "trigger": spec["trigger"],
        "cct": spec["cct"],
        "cst": spec["cst"],
        "reward": spec["reward"],
    }


def settings_fingerprint(spec: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(effective_settings(spec)).encode("utf-8")).hexdigest()


def load_spec(path: Path) -> dict[str, Any]:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read spec JSON at {path}: {exc}") from exc

    required_top_level = {"version", "instruments", "timeframes", "trigger", "cct", "cst", "reward"}
    missing = required_top_level - set(spec)
    if missing:
        raise ValueError(f"{path}: spec is missing {', '.join(sorted(missing))}")
    if not isinstance(spec["instruments"], list) or not spec["instruments"]:
        raise ValueError(f"{path}: instruments must be a non-empty array")
    if not isinstance(spec["timeframes"], list) or not spec["timeframes"]:
        raise ValueError(f"{path}: timeframes must be a non-empty array")
    if spec["cst"].get("break_basis") not in {"close", "wick"}:
        raise ValueError(f"{path}: cst.break_basis must be 'close' or 'wick'")
    if int(spec["cct"].get("window", 0)) < 1:
        raise ValueError(f"{path}: cct.window must be positive")
    if int(spec["cct"].get("swing_neighbors", 0)) < 1:
        raise ValueError(f"{path}: cct.swing_neighbors must be positive")

    reward = spec["reward"]
    if int(reward.get("lookahead_candles", 0)) < 1:
        raise ValueError(f"{path}: reward.lookahead_candles must be positive")
    if reward.get("same_candle_tie") not in {"adverse_first", "target_first"}:
        raise ValueError(f"{path}: reward.same_candle_tie is invalid")
    validation = reward.get("validation", {})
    total_months = int(validation.get("total_months", 0))
    if (
        total_months < 1
        or int(validation.get("training_months", 0))
        + int(validation.get("validation_months", 0))
        != total_months
    ):
        raise ValueError(f"{path}: reward.validation month counts are inconsistent")
    for symbol in spec["instruments"]:
        threshold = reward.get("thresholds", {}).get(symbol)
        if not threshold:
            raise ValueError(f"{path}: no reward threshold configured for {symbol}")
        for name in ("d_min", "a_max"):
            try:
                value = Decimal(str(threshold[name]))
            except (KeyError, ArithmeticError, ValueError) as exc:
                raise ValueError(f"{path}: invalid {symbol} reward threshold {name}") from exc
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{path}: {symbol} {name} must be finite and positive")
    return spec


def candle_objects(records: list[dict[str, Any]]) -> list[Candle]:
    return [
        Candle.from_values(
            record["time"],
            record["open"],
            record["high"],
            record["low"],
            record["close"],
        )
        for record in records
    ]


def decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _month_key(time_value: str) -> str:
    return time_value[:7]


def _month_ordinal(month_key: str) -> int:
    year, month = (int(part) for part in month_key.split("-"))
    return year * 12 + month - 1


def _month_key_from_ordinal(ordinal: int) -> str:
    year, month_index = divmod(ordinal, 12)
    return f"{year:04d}-{month_index + 1:02d}"


def last_calendar_month_window(
    loaded: dict[tuple[str, str], CandleFileData],
    total_months: int,
) -> tuple[list[str], list[str]]:
    present_by_pair = {
        pair: {_month_key(record["time"]) for record in file_data.candles}
        for pair, file_data in loaded.items()
    }
    present_months = set().union(*present_by_pair.values()) if present_by_pair else set()
    if not present_months:
        return [], []
    latest = max(_month_ordinal(month) for month in present_months)
    months = [
        _month_key_from_ordinal(ordinal)
        for ordinal in range(latest - total_months + 1, latest + 1)
    ]
    missing = [month for month in months if month not in present_months]
    return months, missing


def period_for_time(time_value: str, months: list[str], training_months: int) -> str:
    if not months:
        return "UNAVAILABLE"
    month = _month_key(time_value)
    if month not in months:
        return "OUTSIDE_WINDOW"
    return "TRAIN" if months.index(month) < training_months else "VALIDATION"


def filter_candles_by_date_window(
    candles: list[dict[str, Any]],
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    if from_date > to_date:
        raise ValueError("--from must be on or before --to.")
    filtered = [
        candle
        for candle in candles
        if from_date
        <= date.fromisoformat(str(candle["time"])[:10])
        <= to_date
    ]
    if not filtered:
        raise ValueError(
            f"No candles found in date window {from_date.isoformat()} "
            f"through {to_date.isoformat()}."
        )
    return filtered


def _decimal(value: Any, setting: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise ValueError(f"Invalid decimal in {setting}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite decimal in {setting}")
    return result


def run_pair(
    candles: list[Candle],
    *,
    symbol: str,
    timeframe: str,
    source: str,
    input_sha256: str,
    spec: dict[str, Any],
    months: list[str],
    run_id: str,
    config_fingerprint: str,
) -> list[dict[str, str]]:
    cct_settings = spec["cct"]
    cst_settings = spec["cst"]
    reward_settings = spec["reward"]
    threshold = reward_settings["thresholds"][symbol]
    d_min = _decimal(threshold["d_min"], f"reward.thresholds.{symbol}.d_min")
    a_max = _decimal(threshold["a_max"], f"reward.thresholds.{symbol}.a_max")
    lookahead = int(reward_settings["lookahead_candles"])
    speed_bonus = reward_settings["speed_bonus"]
    follow_through = reward_settings["follow_through_bonus"]
    cst_states = compute_cst(
        candles,
        swing_neighbors=int(cst_settings["major_swing_neighbors"]),
        break_basis=cst_settings["break_basis"],
    )

    alerts: list[dict[str, str]] = []
    for index in range(1, len(candles)):
        previous = candles[index - 1]
        current = candles[index]
        typed_signals: list[tuple[str, str]] = []
        direction = detect_trigger(previous, current)
        if direction is not None:
            typed_signals.append((str(direction), f"{direction}-A"))
        variant_b = detect_trigger_b(previous, current)
        if variant_b is not None:
            variant_direction = "SELL" if str(variant_b) == "SELL-B" else "BUY"
            typed_signals.append((variant_direction, str(variant_b)))
        if not typed_signals:
            continue

        cct_window = int(cct_settings["window"])
        context = candles[max(0, index + 1 - cct_window) : index + 1]
        cst_state = cst_states[index]
        cct = classify_cct(
            context,
            window=cct_window,
            swing_neighbors=int(cct_settings["swing_neighbors"]),
        )
        for signal_direction, alert_type in typed_signals:
            reward_score, reward_status = score_alert(
                signal_direction,  # type: ignore[arg-type]
                current.close,
                candles[index + 1 : index + 1 + lookahead],
                d_min=d_min,
                a_max=a_max,
                lookahead=lookahead,
                speed_bonus_candles=int(speed_bonus["within_candles"]),
                same_candle_tie=reward_settings["same_candle_tie"],
                follow_through_per_unit=_decimal(
                    follow_through["score_per_unit"], "reward.follow_through_bonus.score_per_unit"
                ),
                follow_through_cap=_decimal(
                    follow_through["maximum_score"], "reward.follow_through_bonus.maximum_score"
                ),
            )
            alerts.append(
                {
                    "time": current.time,
                    "symbol": symbol,
                    "tf": timeframe,
                    "direction": signal_direction,
                    "alert_type": alert_type,
                    "cct": str(cct),
                    "cst": str(cst_state),
                    "strength_label": str(strength_for(signal_direction, cst_state)),  # type: ignore[arg-type]
                    "note": smaller_body_note(previous, current) or "",
                    "period": period_for_time(
                        current.time,
                        months,
                        int(reward_settings["validation"]["training_months"]),
                    ),
                    "reward_score": decimal_text(reward_score),
                    "reward_status": reward_status,
                    "spec_version": str(spec["version"]),
                    "spec_status": str(spec.get("status", "unspecified")),
                    "settings_fingerprint": config_fingerprint,
                    "cst_settings_json": canonical_json(cst_settings),
                    "reward_settings_json": canonical_json(reward_settings),
                    "input_source": source,
                    "input_sha256": input_sha256,
                    "run_id": run_id,
                }
            )
    return alerts


def run_pair_smoke(
    candles: list[Candle],
    *,
    symbol: str,
    timeframe: str,
    source: str,
    input_sha256: str,
    spec: dict[str, Any],
    run_id: str,
    config_fingerprint: str,
) -> list[dict[str, str]]:
    """Generate replayable alerts without assigning periods or scoring rewards."""
    cct_settings = spec["cct"]
    cst_settings = spec["cst"]
    cst_states = compute_cst(
        candles,
        swing_neighbors=int(cst_settings["major_swing_neighbors"]),
        break_basis=cst_settings["break_basis"],
    )
    alerts: list[dict[str, str]] = []
    for index in range(1, len(candles)):
        previous = candles[index - 1]
        current = candles[index]
        typed_signals: list[tuple[str, str]] = []
        direction = detect_trigger(previous, current)
        if direction is not None:
            typed_signals.append((str(direction), f"{direction}-A"))
        variant_b = detect_trigger_b(previous, current)
        if variant_b is not None:
            variant_direction = "SELL" if str(variant_b) == "SELL-B" else "BUY"
            typed_signals.append((variant_direction, str(variant_b)))
        if not typed_signals:
            continue
        context = candles[
            max(0, index + 1 - int(cct_settings["window"])) : index + 1
        ]
        cst_state = cst_states[index]
        cct = classify_cct(
            context,
            window=int(cct_settings["window"]),
            swing_neighbors=int(cct_settings["swing_neighbors"]),
        )
        for signal_direction, alert_type in typed_signals:
            alerts.append(
                {
                    "time": current.time,
                    "symbol": symbol,
                    "tf": timeframe,
                    "direction": signal_direction,
                    "alert_type": alert_type,
                    "cct": str(cct),
                    "cst": str(cst_state),
                    "strength_label": str(strength_for(signal_direction, cst_state)),  # type: ignore[arg-type]
                    "note": smaller_body_note(previous, current) or "",
                    "period": "SMOKE",
                    "reward_score": "",
                    "reward_status": "NOT_SCORED",
                    "spec_version": str(spec["version"]),
                    "spec_status": str(spec.get("status", "unspecified")),
                    "settings_fingerprint": config_fingerprint,
                    "cst_settings_json": canonical_json(cst_settings),
                    "reward_settings_json": canonical_json(spec["reward"]),
                    "input_source": source,
                    "input_sha256": input_sha256,
                    "run_id": run_id,
                }
            )
    return alerts


def validation_summary(
    alerts: list[dict[str, str]],
    *,
    spec: dict[str, Any],
    months: list[str],
    missing_months: list[str],
    evaluated_pairs: set[tuple[str, str]] | None = None,
    skipped_pairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reward = spec["reward"]
    validation = reward["validation"]
    floors = reward["floors"]
    total_months = int(validation["total_months"])
    training_months = int(validation["training_months"])
    validation_months = int(validation["validation_months"])
    complete_window = len(months) == total_months and not missing_months

    expected_pairs = [
        (symbol, timeframe)
        for symbol in spec["instruments"]
        for timeframe in spec["timeframes"]
    ]
    evaluated_pairs = (
        set(expected_pairs) if evaluated_pairs is None else set(evaluated_pairs)
    )
    skip_reasons = {
        (item["symbol"], item["timeframe"]): item["reason"]
        for item in (skipped_pairs or [])
    }
    pair_counts: dict[str, dict[str, Any]] = {}
    for symbol, timeframe in expected_pairs:
        minimum = int(floors["minimum_validation_alerts_per_symbol_timeframe"])
        if (symbol, timeframe) not in evaluated_pairs:
            pair_counts[f"{symbol}_{timeframe}"] = {
                "validation_alerts": 0,
                "minimum_alerts": minimum,
                "floor_status": "NOT_EVALUATED",
                "pair_status": "SKIPPED",
                "skip_reason": skip_reasons.get(
                    (symbol, timeframe), "missing_input"
                ),
                "scored_alerts": 0,
                "insufficient_lookahead_alerts": 0,
            }
            continue
        matching = [
            alert
            for alert in alerts
            if alert["symbol"] == symbol
            and alert["tf"] == timeframe
            and alert["period"] == "VALIDATION"
        ]
        count = len(matching)
        pair_counts[f"{symbol}_{timeframe}"] = {
            "validation_alerts": count,
            "minimum_alerts": minimum,
            "floor_status": "NOT_EVALUATED"
            if not complete_window
            else ("PASS" if count >= minimum else "FAIL"),
            "scored_alerts": sum(alert["reward_status"] == "SCORED" for alert in matching),
            "insufficient_lookahead_alerts": sum(
                alert["reward_status"] == "INSUFFICIENT_LOOKAHEAD" for alert in matching
            ),
        }

    covered_months = sorted(
        {
            _month_key(alert["time"])
            for alert in alerts
            if _month_key(alert["time"]) in months
        }
    )
    coverage_minimum = int(floors["minimum_months_with_alerts"])
    coverage_status = (
        "NOT_EVALUATED"
        if not complete_window
        else ("PASS" if len(covered_months) >= coverage_minimum else "FAIL")
    )
    evaluated_floor_results = [
        pair_counts[f"{symbol}_{timeframe}"]
        for symbol, timeframe in expected_pairs
        if (symbol, timeframe) in evaluated_pairs
    ]
    pair_floors_pass = bool(evaluated_floor_results) and complete_window and all(
        result["floor_status"] == "PASS" for result in evaluated_floor_results
    )
    all_floors_pass = pair_floors_pass and coverage_status == "PASS"
    if not complete_window or not evaluated_floor_results:
        all_floors_status = "NOT_EVALUATED"
    elif skipped_pairs:
        all_floors_status = "NOT_EVALUATED_SKIPPED_PAIRS"
    else:
        all_floors_status = "PASS" if all_floors_pass else "FAIL"

    if complete_window:
        validation_alerts = [alert for alert in alerts if alert["period"] == "VALIDATION"]
        training_alerts = [alert for alert in alerts if alert["period"] == "TRAIN"]
        total_reward = sum(
            (
                Decimal(alert["reward_score"])
                for alert in validation_alerts
                if alert["reward_status"] == "SCORED"
            ),
            start=Decimal("0"),
        )
        objective_score: str | None = decimal_text(total_reward)
        training_reward = sum(
            (
                Decimal(alert["reward_score"])
                for alert in training_alerts
                if alert["reward_status"] == "SCORED"
            ),
            start=Decimal("0"),
        )
        status = "READY"
    else:
        validation_alerts = []
        training_alerts = []
        objective_score = None
        training_reward = Decimal("0")
        status = "INSUFFICIENT_12_CONSECUTIVE_MONTHS"

    training_failures = sorted(
        (
            alert
            for alert in training_alerts
            if alert["reward_status"] == "INSUFFICIENT_LOOKAHEAD"
            or (
                alert["reward_status"] == "SCORED"
                and Decimal(alert["reward_score"]) <= 0
            )
        ),
        key=lambda alert: (alert["time"], alert["symbol"], alert["tf"]),
    )[-20:]

    validation_rows_for_strata = [
        alert for alert in alerts if alert["period"] == "VALIDATION"
    ]

    def summarize_strength(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for label in ("STRONG", "NEUTRAL", "WEAK"):
            matching = [
                alert for alert in rows if alert.get("strength_label") == label
            ]
            scored = [
                alert for alert in matching if alert["reward_status"] == "SCORED"
            ]
            reward_total = sum(
                (Decimal(alert["reward_score"]) for alert in scored),
                start=Decimal("0"),
            )
            result[label] = {
                "validation_alerts": len(matching),
                "validation_alerts_scored": len(scored),
                "validation_alerts_insufficient_lookahead": sum(
                    alert["reward_status"] == "INSUFFICIENT_LOOKAHEAD"
                    for alert in matching
                ),
                "total_validation_reward": decimal_text(reward_total)
                if complete_window
                else None,
            }
        return result

    strength_by_pair = {
        f"{symbol}_{timeframe}": summarize_strength(
            [
                alert
                for alert in validation_rows_for_strata
                if alert["symbol"] == symbol and alert["tf"] == timeframe
            ]
        )
        for symbol, timeframe in expected_pairs
    }
    strength_overall = summarize_strength(validation_rows_for_strata)
    alerts_by_timeframe = {}
    for timeframe in spec["timeframes"]:
        timeframe_alerts = [alert for alert in alerts if alert["tf"] == timeframe]
        timeframe_validation = [
            alert for alert in timeframe_alerts if alert["period"] == "VALIDATION"
        ]
        alerts_by_timeframe[timeframe] = {
            "alerts": len(timeframe_alerts),
            "validation_alerts": len(timeframe_validation),
            "validation_alerts_scored": sum(
                alert["reward_status"] == "SCORED"
                for alert in timeframe_validation
            ),
            "validation_alerts_insufficient_lookahead": sum(
                alert["reward_status"] == "INSUFFICIENT_LOOKAHEAD"
                for alert in timeframe_validation
            ),
        }

    return {
        "status": status,
        "evaluated_pairs": [
            {"symbol": symbol, "timeframe": timeframe}
            for symbol, timeframe in sorted(evaluated_pairs)
        ],
        "coverage_window_months": months,
        "missing_months": missing_months,
        "training_months": months[:training_months] if complete_window else [],
        "validation_months": months[training_months : training_months + validation_months]
        if complete_window
        else [],
        "validation_alerts": len(validation_alerts),
        "validation_alerts_scored": sum(
            alert["reward_status"] == "SCORED" for alert in validation_alerts
        ),
        "validation_alerts_insufficient_lookahead": sum(
            alert["reward_status"] == "INSUFFICIENT_LOOKAHEAD"
            for alert in validation_alerts
        ),
        "alerts_by_timeframe": alerts_by_timeframe,
        "total_validation_reward": objective_score,
        "training": {
            "alerts": len(training_alerts),
            "scored_alerts": sum(
                alert["reward_status"] == "SCORED" for alert in training_alerts
            ),
            "insufficient_lookahead_alerts": sum(
                alert["reward_status"] == "INSUFFICIENT_LOOKAHEAD"
                for alert in training_alerts
            ),
            "total_reward": decimal_text(training_reward)
            if complete_window
            else None,
        },
        "training_failure_examples": [
            {
                "time": alert["time"],
                "symbol": alert["symbol"],
                "timeframe": alert["tf"],
                "direction": alert["direction"],
                "cct": alert["cct"],
                "cst": alert["cst"],
                "strength_label": alert["strength_label"],
                "reward_score": alert["reward_score"],
                "reward_status": alert["reward_status"],
            }
            for alert in training_failures
        ],
        "pair_floors": pair_counts,
        "strength_label_stratified": {
            "by_symbol_timeframe": strength_by_pair,
            "overall": strength_overall,
            "note": (
                "The total validation reward objective is blind to CST strength. "
                "Strength labels do not change which alerts fire, so this split "
                "is descriptive only. Alert counts include INSUFFICIENT_LOOKAHEAD "
                "rows; reward sums include only scored rows."
            ),
        },
        "month_coverage": {
            "scope": floors["month_coverage_scope"],
            "covered_months": covered_months,
            "covered_count": len(covered_months),
            "required_count": coverage_minimum,
            "floor_status": coverage_status,
        },
        "all_floors_status": all_floors_status,
        "note": (
            "Synthetic-data scores are software checks, not historical results or "
            "evidence of profitability."
        ),
    }


def write_alerts(path: Path, alerts: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=ALERT_FIELDS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(alerts)


def _run_id(
    fingerprint: str,
    loaded: dict[tuple[str, str], CandleFileData],
) -> str:
    input_hashes = {
        f"{symbol}_{timeframe}": data.sha256
        for (symbol, timeframe), data in sorted(loaded.items())
    }
    payload = {"settings_fingerprint": fingerprint, "input_hashes": input_hashes}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def provenance_kind(source: str) -> str:
    normalized = source.strip().lower()
    if normalized == "synthetic":
        return "synthetic"
    if normalized == "unverified_real":
        return "unverified"
    return "real"


def synthetic_csv_paths(data_dir: Path, synthetic_dir: Path) -> list[Path]:
    paths = (
        {
            path
            for path in synthetic_dir.rglob("*")
            if path.is_file() and path.suffix.lower() == ".csv"
        }
        if synthetic_dir.is_dir()
        else set()
    )
    paths.update(
        path for path in data_dir.glob("synthetic_*.csv") if path.is_file()
    )
    return sorted(paths)


def synthetic_candidates_for_pair(
    data_dir: Path,
    synthetic_dir: Path,
    symbol: str,
    timeframe: str,
) -> list[Path]:
    stem = f"{symbol.upper()}_{timeframe.upper()}"
    candidates = {
        synthetic_dir / f"{stem}.csv",
        synthetic_dir / f"synthetic_{stem}.csv",
        data_dir / f"synthetic_{stem}.csv",
    }
    return sorted(path for path in candidates if path.is_file())


def synthetic_file_record(
    path: Path,
    *,
    reason: str,
    data: CandleFileData | None = None,
) -> dict[str, Any]:
    source = data.source if data is not None else "synthetic"
    return {
        "file": str(path),
        "source": source,
        "provenance": provenance_kind(source),
        "sha256": data.sha256 if data is not None else sha256_file(path),
        "bytes": path.stat().st_size,
        "candles": len(data.candles) if data is not None else None,
        "exclusion_reason": reason,
    }


def build_report(
    *,
    spec: dict[str, Any],
    spec_path: Path,
    loaded: dict[tuple[str, str], CandleFileData],
    alerts: list[dict[str, str]],
    months: list[str],
    missing_months: list[str],
    output_path: Path | None,
    report_path: Path,
    run_id: str,
    config_fingerprint: str,
    skipped_pairs: list[dict[str, Any]] | None = None,
    excluded_synthetic_files: list[dict[str, Any]] | None = None,
    unapproved_real_inputs: list[dict[str, Any]] | None = None,
    run_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skipped_pairs = skipped_pairs or []
    excluded_synthetic_files = excluded_synthetic_files or []
    unapproved_real_inputs = unapproved_real_inputs or []
    source_provenance: list[dict[str, Any]] = []
    for (symbol, timeframe), data in sorted(loaded.items()):
        source_provenance.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "source": data.source,
                "provenance": provenance_kind(data.source),
                "file": str(data.path),
                "sha256": data.sha256,
                "candles": len(data.candles),
                "first_time_utc": data.candles[0]["time"],
                "last_time_utc": data.candles[-1]["time"],
                "sanity_report": str(sanity_manifest_path(data.path.parent, symbol, timeframe))
                if data.source != "synthetic"
                else None,
            }
        )

    validation_report = validation_summary(
        alerts,
        spec=spec,
        months=months,
        missing_months=missing_months,
        evaluated_pairs=set(loaded),
        skipped_pairs=skipped_pairs,
    )
    validation_report["input_month_coverage_by_pair"] = {
        f"{symbol}_{timeframe}": {
            "available_months": sorted({_month_key(row["time"]) for row in data.candles}),
            "missing_window_months": [
                month
                for month in months
                if month not in {_month_key(row["time"]) for row in data.candles}
            ],
        }
        for (symbol, timeframe), data in sorted(loaded.items())
    }
    has_synthetic_data = any(data.source == "synthetic" for data in loaded.values())
    validation_report["score_context"] = (
        "SYNTHETIC_SOFTWARE_CHECK_ONLY"
        if has_synthetic_data
        else (
            "USER_REQUESTED_UNREVIEWED_REAL_DATA"
            if unapproved_real_inputs
            else "OWNER_APPROVED_REAL_DATA"
        )
    )
    validation_report["real_data_review_override"] = {
        "used": bool(unapproved_real_inputs),
        "unapproved_inputs": unapproved_real_inputs,
    }
    validation_report["note"] = (
        "Synthetic-data scores are software checks only, not market evaluation "
        "or evidence of profitability."
        if has_synthetic_data
        else (
            "The explicit CLI override was used while real-data review was pending."
            if unapproved_real_inputs
            else "Real-data scores were enabled only after owner approval of hash-matched source checks."
        )
    )

    return {
        "report_schema_version": 3,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "spec": {
            "name": spec.get("name"),
            "version": str(spec["version"]),
            "status": str(spec.get("status", "unspecified")),
            "frozen": bool(spec.get("frozen", False)),
            "file": str(spec_path),
            "settings_fingerprint": config_fingerprint,
            "effective_settings": effective_settings(spec),
        },
        "data_provenance": source_provenance,
        "run_overrides": run_overrides or {},
        "skipped_pairs": skipped_pairs,
        "excluded_synthetic_files": excluded_synthetic_files,
        "unapproved_real_inputs": unapproved_real_inputs,
        "alert_log": {
            "file": str(output_path) if output_path else None,
            "written": output_path is not None,
            "rows": len(alerts),
            "settings_columns": [
                "spec_version",
                "spec_status",
                "settings_fingerprint",
                "cst_settings_json",
                "reward_settings_json",
            ],
        },
        "validation": validation_report,
        "warnings": [
            "Alerts are informational only and are not trading recommendations.",
            "No profitability or probability-of-success claim is made.",
        ]
        + (
            ["Synthetic candles are software fixtures, not market history."]
            if any(data.source == "synthetic" for data in loaded.values())
            else []
        )
        + (
            ["The explicit CLI override was used while real-data review was pending."]
            if unapproved_real_inputs
            else []
        ),
        "provisional_seed_note": (
            (
                "Seed settings are owner-approved but remain unfrozen."
                if spec.get("status") == "owner_approved_unfrozen"
                else "Seed settings are pending explicit owner sign-off."
            )
            if not spec.get("frozen", False)
            else None
        ),
        "report_file": str(report_path),
    }


def print_report(report: dict[str, Any]) -> None:
    spec = report["spec"]
    validation = report["validation"]
    has_synthetic_data = any(
        item["source"] == "synthetic" for item in report["data_provenance"]
    )
    print("\nSTRATEGY LAB BACKTEST")
    print("Alerts are informational only. This is not a trade recommendation.")
    if has_synthetic_data:
        print(
            "SYNTHETIC DATA ONLY — any score below is a software check, "
            "not a market result."
        )
    print(f"Spec version/status: {spec['version']} / {spec['status']}")
    print(f"Settings fingerprint: {spec['settings_fingerprint']}")
    print("CST settings:")
    print(json.dumps(spec["effective_settings"]["cst"], indent=2, sort_keys=True))
    print("Reward settings:")
    print(json.dumps(spec["effective_settings"]["reward"], indent=2, sort_keys=True))
    print("-" * 76)
    for item in report["data_provenance"]:
        print(
            f"{item['symbol']} {item['timeframe']}: {item['candles']:,} candles, "
            f"source={item['source']}, sha256={item['sha256'][:16]}..."
        )
    for item in report.get("skipped_pairs", []):
        print(
            f"Skipped pair: {item['symbol']} {item['timeframe']} "
            f"({item['reason']})"
        )
    print(f"Run ID: {report['run_id']}")
    if validation["status"] != "READY":
        print(
            "Validation objective: NOT REPORTED — fewer than 12 consecutive "
            "calendar months are available."
        )
        if validation["missing_months"]:
            print(f"Missing months: {', '.join(validation['missing_months'])}")
    else:
        score_label = (
            "Synthetic-only validation reward"
            if has_synthetic_data
            else "Total validation reward"
        )
        print(f"{score_label}: {validation['total_validation_reward']}")
        print(
            "Month coverage: "
            f"{validation['month_coverage']['covered_count']}/"
            f"{len(validation['coverage_window_months'])} "
            f"({validation['month_coverage']['floor_status']})"
        )
        for key, result in validation["pair_floors"].items():
            print(
                f"{key}: {result['validation_alerts']} validation alerts "
                f"(minimum {result['minimum_alerts']}) — {result['floor_status']}"
            )
        print(f"All validation floors: {validation['all_floors_status']}")
    if report["provisional_seed_note"]:
        print(f"PROVISIONAL: {report['provisional_seed_note']}")
    for warning in report["warnings"]:
        print(f"WARNING: {warning}")
    if report["alert_log"]["written"]:
        print(f"Alert log: {report['alert_log']['file']}")
    else:
        print("Alert log: not written (summary-only mode)")
    print(f"Full settings and report: {report['report_file']}")


def run_smoke_window(
    *,
    loaded: dict[tuple[str, str], CandleFileData],
    spec: dict[str, Any],
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    """Return an unscored review window in memory without writing sliced data."""
    if __package__:
        from .replay import StreamingWickPressure, build_mtf_context
    else:
        from replay import StreamingWickPressure, build_mtf_context

    if not loaded:
        raise ValueError("No candle inputs were loaded.")
    filtered_by_pair = {
        pair: filter_candles_by_date_window(data.candles, from_date, to_date)
        for pair, data in loaded.items()
    }
    settings_digest = settings_fingerprint(spec)
    run_id = _run_id(settings_digest, loaded)
    all_alerts: list[dict[str, Any]] = []

    for (symbol, timeframe), file_data in sorted(loaded.items()):
        selected_candles = filtered_by_pair[(symbol, timeframe)]
        mtf_context = (
            build_mtf_context(
                selected_candles,
                loaded.get((symbol, "M15")).candles
                if timeframe == "M5" and loaded.get((symbol, "M15"))
                else [],
                spec=spec,
            )
            if timeframe == "M5"
            else None
        )
        candle_objects_for_pair = candle_objects(selected_candles)
        shadow_states = compute_shadow_states(candle_objects_for_pair)
        pressure_stream = StreamingWickPressure()
        pressure_states = [
            str(pressure_stream.update(candle))
            for candle in candle_objects_for_pair
        ]
        pair_alerts = run_pair_smoke(
            candle_objects_for_pair,
            symbol=symbol,
            timeframe=timeframe,
            source=file_data.source,
            input_sha256=file_data.sha256,
            spec=spec,
            run_id=run_id,
            config_fingerprint=settings_digest,
        )
        candle_index = {
            str(candle["time"]): index
            for index, candle in enumerate(selected_candles)
        }
        optimizer_settings = spec.get("optimizer_settings", {})
        if not isinstance(optimizer_settings, dict):
            optimizer_settings = {}
        for alert in pair_alerts:
            index = candle_index[alert["time"]]
            direction = str(alert["direction"])
            current_candle = candle_objects_for_pair[index]
            previous_candle = candle_objects_for_pair[index - 1]
            engulfing = body_engulfs_previous(
                previous_candle, current_candle, direction  # type: ignore[arg-type]
            )
            pressure = pressure_states[index]
            alert["dc_state"] = shadow_states[index]["dc_state"]
            alert["hier_state"] = shadow_states[index]["hier_state"]
            alert["hier_promotions"] = shadow_states[index]["hier_promotions"]
            mtf = mtf_context[index] if mtf_context is not None else {}
            mtf_cst = mtf.get("mtf_cst")
            mtf_cct = mtf.get("mtf_cct")
            mtf_wp = mtf.get("mtf_wick_pressure")
            direction = str(alert["direction"])
            alert["mtf_cst"] = mtf_cst
            alert["mtf_cct"] = mtf_cct
            alert["mtf_wick_pressure"] = mtf_wp
            alert["mtf_closed_at"] = mtf.get("mtf_closed_at")
            alert["mtf_trend_agree"] = mtf_cst == (
                "uptrend" if direction == "BUY" else "downtrend"
            )
            alert["mtf_cct_agree"] = mtf_cct == (
                "uptrend" if direction == "BUY" else "downtrend"
            )
            alert["mtf_wp_agree"] = mtf_wp == (
                "up-pressure" if direction == "BUY" else "down-pressure"
            )
            alert["dual_tf_alert"] = bool(
                mtf.get("dual_tf_directions", {}).get(direction, False)
            )
            strength_score, strength_label, strength_features = calculate_strength(
                direction,  # type: ignore[arg-type]
                cct=str(alert["cct"]),
                cst=str(alert["cst"]),
                wick_pressure=pressure,
                engulfing=engulfing,
                mtf_cst=mtf_cst,
                mtf_wick_pressure=mtf_wp,
                dual_tf_alert=alert["dual_tf_alert"],
                weights=optimizer_settings.get("strength_weights"),
                thresholds=optimizer_settings.get("strength_thresholds"),
            )
            alert["wick_pressure"] = pressure
            alert["engulfing"] = engulfing
            alert["strength_score"] = str(strength_score)
            alert["strength_features"] = strength_features
            alert["strength_label"] = strength_label
            identity = {
                "input_sha256": file_data.sha256,
                "symbol": symbol,
                "timeframe": timeframe,
                "time": alert["time"],
                "direction": alert["direction"],
            }
            alert["alert_id"] = hashlib.sha256(
                canonical_json(identity).encode("utf-8")
            ).hexdigest()
            alert["candle"] = selected_candles[index]
            alert["previous_candle"] = selected_candles[index - 1]
            alert["scored"] = False
        all_alerts.extend(pair_alerts)

    return {
        "candles_by_pair": filtered_by_pair,
        "alerts": all_alerts,
        "run_id": run_id,
        "settings_fingerprint": settings_digest,
    }


def run_smoke_backtest(
    *,
    loaded: dict[tuple[str, str], CandleFileData],
    spec: dict[str, Any],
    from_date: date,
    to_date: date,
    started_at: float,
    output_dir: Path = DEFAULT_SMOKE_OUTPUT_DIR,
) -> int:
    result = run_smoke_window(
        loaded=loaded,
        spec=spec,
        from_date=from_date,
        to_date=to_date,
    )
    all_alerts = result["alerts"]
    candle_count = sum(
        len(candles) for candles in result["candles_by_pair"].values()
    )
    write_alerts(output_dir / "alerts.csv.gz", all_alerts)
    direction_counts = {"BUY": 0, "SELL": 0}
    strength_counts = {"STRONG": 0, "NEUTRAL": 0, "WEAK": 0}
    for alert in all_alerts:
        direction_counts[alert["direction"]] = (
            direction_counts.get(alert["direction"], 0) + 1
        )
        strength_counts[alert["strength_label"]] = (
            strength_counts.get(alert["strength_label"], 0) + 1
        )

    elapsed = time.perf_counter() - started_at
    print("SMOKE TEST — split and floors not evaluated")
    print(f"Candles: {candle_count}")
    print(
        "Alerts by direction: "
        + ", ".join(f"{key}={value}" for key, value in direction_counts.items())
    )
    print(
        "Alerts by strength label: "
        + ", ".join(f"{key}={value}" for key, value in strength_counts.items())
    )
    print(f"Wall-clock time: {elapsed:.3f} seconds")
    return 0


def parse_iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backtest a versioned candle strategy against local OHLC CSVs."
    )
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--timeframes",
        nargs="+",
        type=str.upper,
        choices=("M1", "M5", "M15", "H1", "H4", "D1"),
        help="Run-time timeframe override; defaults to the frozen spec list.",
    )
    parser.add_argument("--from", dest="from_date", type=parse_iso_date, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", type=parse_iso_date, metavar="YYYY-MM-DD")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Compute the full report without writing the large alert CSV.",
    )
    args = parser.parse_args(argv)
    smoke_mode = args.from_date is not None or args.to_date is not None
    if (args.from_date is None) != (args.to_date is None):
        parser.error("--from and --to must be provided together.")
    if smoke_mode and args.from_date > args.to_date:
        parser.error("--from must be on or before --to.")
    started_at = time.perf_counter()

    try:
        spec = load_spec(args.spec)
        effective_timeframes = args.timeframes or list(spec["timeframes"])
        if len(set(effective_timeframes)) != len(effective_timeframes):
            raise ValueError("--timeframes cannot contain duplicates.")
        run_spec = {**spec, "timeframes": effective_timeframes}
        loaded: dict[tuple[str, str], CandleFileData] = {}
        skipped_pairs: list[dict[str, str]] = []
        for symbol in run_spec["instruments"]:
            for timeframe in effective_timeframes:
                try:
                    file_data = load_candles_with_metadata(
                        symbol,
                        timeframe,
                        data_dir=args.data_dir,
                    )
                except FileNotFoundError as exc:
                    skipped_pairs.append(
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "reason": str(exc),
                        }
                    )
                    if not smoke_mode:
                        print(f"Skipping {symbol} {timeframe}: {exc}")
                    continue
                monthly_file = (
                    file_data.path.parent
                    == args.data_dir / symbol.upper() / timeframe.upper()
                )
                if file_data.source != "synthetic" and not monthly_file:
                    if timeframe == "M1":
                        require_owner_approved_sanity(
                            args.data_dir,
                            symbol=symbol,
                            timeframe=timeframe,
                            primary_sha256=file_data.sha256,
                        )
                    else:
                        require_owner_approved_derived_timeframe(
                            args.data_dir,
                            symbol=symbol,
                            timeframe=timeframe,
                        )
                loaded[(symbol, timeframe)] = file_data

        if not loaded:
            raise ValueError(f"No input CSV files were found in {args.data_dir}.")

        if smoke_mode:
            return run_smoke_backtest(
                loaded=loaded,
                spec=run_spec,
                from_date=args.from_date,
                to_date=args.to_date,
                started_at=started_at,
            )

        settings_digest = settings_fingerprint(run_spec)
        run_id = _run_id(settings_digest, loaded)
        run_overrides = {
            "timeframes": {
                "frozen_spec": list(spec["timeframes"]),
                "effective": list(effective_timeframes),
                "source": "cli" if args.timeframes else "frozen_spec",
            }
        }
        total_months = int(run_spec["reward"]["validation"]["total_months"])
        months, missing_months = last_calendar_month_window(loaded, total_months)

        all_alerts: list[dict[str, str]] = []
        for (symbol, timeframe), file_data in loaded.items():
            candles = candle_objects(file_data.candles)
            all_alerts.extend(
                run_pair(
                    candles,
                    symbol=symbol,
                    timeframe=timeframe,
                    source=file_data.source,
                    input_sha256=file_data.sha256,
                    spec=run_spec,
                    months=months if not missing_months else [],
                    run_id=run_id,
                    config_fingerprint=settings_digest,
                )
            )

        output_path = None if args.summary_only else args.output
        if output_path is not None:
            write_alerts(output_path, all_alerts)
        report = build_report(
            spec=run_spec,
            spec_path=args.spec,
            loaded=loaded,
            alerts=all_alerts,
            months=months,
            missing_months=missing_months,
            output_path=output_path,
            report_path=args.report,
            run_id=run_id,
            config_fingerprint=settings_digest,
            skipped_pairs=skipped_pairs,
            run_overrides=run_overrides,
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print_report(report)
        return 0
    except (FileNotFoundError, OSError, ValueError, CandleDataError, KeyError, TypeError) as exc:
        print(f"Backtest stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
