"""Versioned, owner-promoted alert notification policy."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

POLICY_SCHEMA_VERSION = 1
POLICY_SETTING_KEYS = (
    "min_strength",
    "require_wp_agree",
    "require_engulf",
    "timeframes",
    "exit_model",
    "cooldown_candles",
)
POLICY_DEFAULT_SETTINGS: dict[str, Any] = {
    "min_strength": "NONE",
    "require_wp_agree": False,
    "require_engulf": False,
    "timeframes": ["M5", "M15"],
    "exit_model": "swept_level_atr",
    "cooldown_candles": 0,
}


def passthrough_policy() -> dict[str, Any]:
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "version": 1,
        "mode": "passthrough",
        "settings": dict(POLICY_DEFAULT_SETTINGS),
        "promoted_at_utc": None,
        "source_champion_version": None,
    }


def validate_policy_settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Alert policy settings must be an object.")
    keys = set(value)
    legacy_keys = set(POLICY_SETTING_KEYS) | {"variant_b"}
    if keys != set(POLICY_SETTING_KEYS) and keys != legacy_keys:
        raise ValueError("Alert policy settings do not match the supported whitelist.")

    minimum_strength = value.get("min_strength")
    if minimum_strength not in {"NONE", "NEUTRAL", "STRONG"}:
        raise ValueError("Policy min_strength must be NONE, NEUTRAL, or STRONG.")
    for key in ("require_wp_agree", "require_engulf"):
        if not isinstance(value.get(key), bool):
            raise ValueError(f"Policy {key} must be a boolean.")
    timeframes = value.get("timeframes")
    if (
        not isinstance(timeframes, list)
        or not timeframes
        or any(item not in {"M5", "M15"} for item in timeframes)
        or len(set(timeframes)) != len(timeframes)
    ):
        raise ValueError("Policy timeframes must be a non-empty subset of M5 and M15.")
    exit_model = value.get("exit_model")
    if exit_model not in {"swept_level_atr", "candle_geometry"}:
        raise ValueError("Policy exit_model is unsupported.")
    cooldown = value.get("cooldown_candles")
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or not 0 <= cooldown <= 48:
        raise ValueError("Policy cooldown_candles must be an integer from 0 through 48.")

    return {
        "min_strength": minimum_strength,
        "require_wp_agree": value["require_wp_agree"],
        "require_engulf": value["require_engulf"],
        "timeframes": [item for item in ("M5", "M15") if item in timeframes],
        "exit_model": exit_model,
        "cooldown_candles": cooldown,
    }


def validate_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Alert policy must be a JSON object.")
    version = value.get("version")
    if (
        value.get("schema_version") != POLICY_SCHEMA_VERSION
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or value.get("mode") not in {"passthrough", "active"}
    ):
        raise ValueError("Alert policy metadata is invalid.")
    promoted_at = value.get("promoted_at_utc")
    champion_version = value.get("source_champion_version")
    if promoted_at is not None and not isinstance(promoted_at, str):
        raise ValueError("Policy promoted_at_utc must be a string or null.")
    if champion_version is not None and not isinstance(champion_version, str):
        raise ValueError("Policy source_champion_version must be a string or null.")
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "version": version,
        "mode": value["mode"],
        "settings": validate_policy_settings(value.get("settings")),
        "promoted_at_utc": promoted_at,
        "source_champion_version": champion_version,
    }


def load_policy(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load alert policy {path.name}.") from exc
    return validate_policy(raw)


def write_promoted_policy(
    *,
    current_path: Path,
    history_dir: Path,
    settings: Mapping[str, Any],
    champion_version: str,
    promoted_at_utc: str,
) -> tuple[dict[str, Any], Path]:
    current = load_policy(current_path)
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive_path = history_dir / f"policy_v{current['version']:04d}_{stamp}.json"
    with archive_path.open("x", encoding="utf-8") as stream:
        json.dump(current, stream, indent=2, sort_keys=True)
        stream.write("\n")

    promoted = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "version": current["version"] + 1,
        "mode": "active",
        "settings": validate_policy_settings(settings),
        "promoted_at_utc": promoted_at_utc,
        "source_champion_version": champion_version,
    }
    temporary_path = current_path.with_name(f".{current_path.name}.tmp")
    current_path.parent.mkdir(parents=True, exist_ok=True)
    with temporary_path.open("w", encoding="utf-8") as stream:
        json.dump(promoted, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary_path.replace(current_path)
    return promoted, archive_path


def policy_rejection_reasons(
    policy: Mapping[str, Any],
    alert: Mapping[str, Any],
    *,
    timeframe: str,
    candle_index: int,
    wick_pressure: str,
    previous_allowed_index: int | None,
) -> list[str]:
    if policy.get("mode") != "active":
        return []

    settings = policy["settings"]
    reasons: list[str] = []
    allowed_timeframes = settings["timeframes"]
    if timeframe not in allowed_timeframes:
        reasons.append(
            f"timeframes={','.join(allowed_timeframes)}: {timeframe} is excluded"
        )

    strength = str(alert.get("strength_label", alert.get("strengthLabel", "")))
    minimum = settings["min_strength"]
    if minimum == "STRONG" and strength != "STRONG":
        reasons.append(f"min_strength=STRONG: alert strength is {strength}")
    elif minimum == "NEUTRAL" and strength == "WEAK":
        reasons.append("min_strength=NEUTRAL: WEAK alerts are excluded")

    if settings["require_wp_agree"]:
        direction = str(alert.get("direction", ""))
        expected = "up-pressure" if direction == "BUY" else "down-pressure"
        if wick_pressure != expected:
            reasons.append(
                f"require_wp_agree=true: {wick_pressure} does not agree with {direction}"
            )

    if settings["require_engulf"] and alert.get("engulfing") is not True:
        reasons.append("require_engulf=true: alert is not engulfing")

    cooldown = settings["cooldown_candles"]
    if cooldown > 0 and previous_allowed_index is not None:
        distance = candle_index - previous_allowed_index
        if distance <= cooldown:
            reasons.append(
                f"cooldown_candles={cooldown}: previous allowed alert was "
                f"{distance} candle(s) ago"
            )
    return reasons
