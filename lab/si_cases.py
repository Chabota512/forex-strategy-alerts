"""File-backed, causal case learning for the XAUUSD System Intelligence engine."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from .capital_context import (
        DEFAULT_CAPITAL_CONTEXT,
        normalize_capital_context,
    )
    from .si_beliefs import (
        MIN_RULE_CASES,
        RULE_DEFINITIONS,
        add_lesson_once,
        adjust_tunables,
        initial_tunables,
        matches_rule,
        score_belief_rules,
        validate_tunables,
    )
    from .versioning import atomic_write_json
else:
    from capital_context import (
        DEFAULT_CAPITAL_CONTEXT,
        normalize_capital_context,
    )
    from si_beliefs import (
        MIN_RULE_CASES,
        RULE_DEFINITIONS,
        add_lesson_once,
        adjust_tunables,
        initial_tunables,
        matches_rule,
        score_belief_rules,
        validate_tunables,
    )
    from versioning import atomic_write_json

DEFAULT_SI_DIR = Path(__file__).resolve().parent / "output" / "system-intelligence"
DEFAULT_CASES_PATH = DEFAULT_SI_DIR / "cases.jsonl"
DEFAULT_STATE_PATH = DEFAULT_SI_DIR / "beliefs.json"
FORMATION_WINDOW = 20
MAX_HISTORY_POINTS = 500
MAX_LESSONS = 200

_RAW_FEATURE_KEYS = frozenset(
    {
        "alert_candle_ohlc",
        "prev_candle_ohlc",
        "candle",
        "previous_candle",
        "input_source",
        "input_sha256",
        "source_sha256",
        "run_id",
    }
)
_AXIS_ALIASES = {
    "cct": ("cct", "cct_state", "cctState"),
    "cst": ("cst", "cst_state", "cstState"),
    "v2": ("cst_v2_state", "cstV2State", "v2_state"),
    "wp": ("wp_state", "wick_pressure", "wickPressure"),
    "mtf_cct": ("mtf_cct", "mtfCct"),
    "mtf_cst": ("mtf_cst", "mtfCst"),
    "mtf_wp": ("mtf_wick_pressure", "mtfWickPressure"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("System Intelligence values must be finite.")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "is_finite") and hasattr(value, "as_tuple"):
        if not value.is_finite():
            raise ValueError("System Intelligence values must be finite.")
        return float(value)
    raise ValueError(f"Unsupported System Intelligence value: {type(value).__name__}.")


def _finite_optional_float(value: Any, label: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number.")
    return number


def _first(mapping: Mapping[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _state_runs(values: Sequence[str]) -> tuple[list[list[Any]], dict[str, int]]:
    runs: list[list[Any]] = []
    transitions: dict[str, int] = {}
    previous: str | None = None
    for value in values:
        if runs and runs[-1][0] == value:
            runs[-1][1] += 1
        else:
            runs.append([value, 1])
        if previous is not None and previous != value:
            transition = f"{previous}>{value}"
            transitions[transition] = transitions.get(transition, 0) + 1
        previous = value
    return runs, dict(sorted(transitions.items()))


def _strength_bucket(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    if number < 0.5:
        return "under_0_5_atr"
    if number < 1:
        return "0_5_to_1_atr"
    if number < 2:
        return "1_to_2_atr"
    return "2_plus_atr"


def build_formation_fingerprint(
    state_history: Sequence[Mapping[str, Any]],
    swing_events: Sequence[Mapping[str, Any]] = (),
    *,
    entry_index: int | None = None,
    window: int = FORMATION_WINDOW,
) -> dict[str, Any]:
    """Compress only confirmed pre-entry states into a compact causal vector."""
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError("Formation window must be a positive whole number.")
    causal_states = [
        state
        for state in state_history
        if entry_index is None
        or (
            isinstance(state.get("index"), int)
            and state["index"] < entry_index
        )
    ][-window:]

    axes: dict[str, dict[str, Any]] = {}
    for axis, aliases in _AXIS_ALIASES.items():
        values = [
            str(_first(state, aliases, "undefined") or "undefined")
            for state in causal_states
        ]
        runs, transitions = _state_runs(values)
        axes[axis] = {"runs": runs, "transitions": transitions}

    swings_by_identity: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    earliest_index = (
        max(0, entry_index - window) if entry_index is not None else None
    )
    for event in swing_events:
        pivot_index = event.get("pivot_index", event.get("pivotIndex"))
        confirmed_index = event.get("confirmed_index", event.get("confirmedIndex"))
        if entry_index is not None:
            if not isinstance(confirmed_index, int) or confirmed_index >= entry_index:
                continue
            if not isinstance(pivot_index, int):
                continue
        if isinstance(pivot_index, int) and earliest_index is not None:
            if not earliest_index <= pivot_index < (entry_index or 0):
                continue
        direction = str(event.get("type", "")).removeprefix("swing_")
        if direction not in {"high", "low"}:
            direction = str(event.get("direction", ""))
        source = str(event.get("source", "unknown"))
        if direction not in {"high", "low"}:
            continue
        if not isinstance(pivot_index, int):
            pivot_index = -1
        identity = (source, direction, pivot_index)
        existing = swings_by_identity.get(identity)
        if existing is None or int(existing.get("confirmed_index", -1)) > int(
            confirmed_index if isinstance(confirmed_index, int) else -1
        ):
            swings_by_identity[identity] = event

    swing_sequence: list[dict[str, Any]] = []
    for event in sorted(
        swings_by_identity.values(),
        key=lambda item: (
            int(item.get("pivot_index", item.get("pivotIndex", -1))),
            str(item.get("source", "")),
            str(item.get("type", "")),
        ),
    ):
        direction = str(event.get("type", "")).removeprefix("swing_")
        if direction not in {"high", "low"}:
            direction = str(event.get("direction", ""))
        item = {
            "direction": direction,
            "source": str(event.get("source", "unknown")),
        }
        strength = _strength_bucket(
            event.get("strength_atr", event.get("strengthAtr"))
        )
        if strength is not None:
            item["strength_atr_bucket"] = strength
        swing_sequence.append(item)

    last_major = [
        item["direction"]
        for item in swing_sequence
        if item["source"] == "cst_major"
    ][-2:]
    return {
        "window": window,
        "state_count": len(causal_states),
        "axes": axes,
        "swing_sequence": swing_sequence,
        "consecutive_lows_reversal": last_major == ["low", "low"],
    }


def cases_from_snapshot(
    snapshot: Mapping[str, Any],
    trades: Sequence[Mapping[str, Any]],
    *,
    source: str,
    source_id: str | None = None,
    formation_fingerprint: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build one compact case per resolved simulated trade, including fades."""
    features = {
        str(key): _json_safe(value)
        for key, value in snapshot.items()
        if key not in _RAW_FEATURE_KEYS
    }
    fingerprint = _json_safe(
        formation_fingerprint
        if formation_fingerprint is not None
        else snapshot.get("formation_fingerprint", {})
    )
    if not isinstance(fingerprint, dict):
        fingerprint = {}
    capital_context = normalize_capital_context(
        snapshot.get("capital_context"), symbol=str(snapshot.get("symbol", "XAUUSD"))
    )

    cases: list[dict[str, Any]] = []
    for trade in trades:
        outcome = str(trade.get("outcome", "")).upper()
        if outcome not in {"WIN", "LOSS"}:
            continue
        symbol = str(trade.get("symbol", snapshot.get("symbol", ""))).upper()
        if symbol != "XAUUSD":
            continue
        try:
            net_r = float(trade.get("netR", trade.get("net_r")))
        except (TypeError, ValueError) as exc:
            raise ValueError("A resolved case requires finite net R.") from exc
        if not math.isfinite(net_r):
            raise ValueError("A resolved case requires finite net R.")
        net_usd = _finite_optional_float(
            trade.get("netPnl", trade.get("net_pnl")), "Resolved case net USD"
        )
        alert_id = str(
            trade.get("alertId", trade.get("alert_id", snapshot.get("alert_id", "")))
        )
        trade_id = str(trade.get("tradeId", trade.get("trade_id", "")))
        if not alert_id or not trade_id:
            raise ValueError("A resolved case requires alert and trade identifiers.")
        case_type = (
            "fade"
            if bool(trade.get("is_fade", trade.get("isFade", False)))
            or trade_id.endswith("-fade")
            else "real"
        )
        config = {
            key: features.get(key)
            for key in (
                "sim_direction_mode",
                "sim_exit_model",
                "sim_geometry_weights",
                "swing_version",
            )
        }
        identity = {
            "alert_id": alert_id,
            "trade_id": trade_id,
            "case_type": case_type,
            "net_r": net_r,
            "net_usd": net_usd,
            "features": features,
            "fingerprint": fingerprint,
            "config": config,
            "capital_context": capital_context,
        }
        case_id = hashlib.sha256(
            json.dumps(
                identity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        cases.append(
            {
                "schema_version": 1,
                "case_id": case_id,
                "alert_id": alert_id,
                "trade_id": trade_id,
                "case_type": case_type,
                "source": source,
                "source_id": source_id,
                "symbol": symbol,
                "timeframe": str(
                    trade.get(
                        "timeframe",
                        features.get("tf", features.get("timeframe", "")),
                    )
                ),
                "entry_time": trade.get("entryTime", trade.get("entry_time")),
                "exit_time": trade.get("exitTime", trade.get("exit_time")),
                "resolved_at_utc": _utc_now(),
                "outcome": outcome,
                "net_r": net_r,
                "net_usd": net_usd,
                "capital_context": capital_context,
                "features": features,
                "formation_fingerprint": fingerprint,
            }
        )
    return cases


class SICaseStore:
    """Idempotent JSONL case base with seeded beliefs and bounded tunables."""

    def __init__(
        self,
        cases_path: Path = DEFAULT_CASES_PATH,
        state_path: Path = DEFAULT_STATE_PATH,
    ) -> None:
        self.cases_path = Path(cases_path)
        self.state_path = Path(state_path)
        self._lock = threading.RLock()
        self._cases = self._load_cases()
        self._case_ids = {str(case["case_id"]) for case in self._cases}
        self._state = self._load_state()
        self._rebuild_metrics()
        self._persist_state()

    def _load_cases(self) -> list[dict[str, Any]]:
        if not self.cases_path.exists():
            return []
        cases: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        with self.cases_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.endswith("\n"):
                    raise ValueError(
                        f"Incomplete System Intelligence case at line {line_number}."
                    )
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid System Intelligence case at line {line_number}."
                    ) from exc
                if not isinstance(value, dict) or not value.get("case_id"):
                    raise ValueError(
                        f"Invalid System Intelligence case at line {line_number}."
                    )
                case_id = str(value["case_id"])
                if case_id in seen_ids:
                    continue
                seen_ids.add(case_id)
                cases.append(value)
        return cases

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        tunables = value.get("tunables")
        if not isinstance(tunables, dict):
            tunables = initial_tunables()
        else:
            tunables = validate_tunables(tunables)
        lessons = value.get("lessons")
        last_tuned_case_count = int(value.get("last_tuned_case_count", 0) or 0)
        legacy_thresholds = {"strong_min": 0.67, "neutral_min": 0.33}
        current_thresholds = tunables.get("strength_thresholds", {})
        if (
            last_tuned_case_count == 0
            and isinstance(current_thresholds, Mapping)
            and math.isclose(
                float(current_thresholds.get("strong_min", -1)),
                legacy_thresholds["strong_min"],
            )
            and math.isclose(
                float(current_thresholds.get("neutral_min", -1)),
                legacy_thresholds["neutral_min"],
            )
        ):
            tunables["strength_thresholds"] = initial_tunables()[
                "strength_thresholds"
            ]
        return {
            "schema_version": 1,
            "tunables": tunables,
            "lessons": (
                [item for item in lessons if isinstance(item, dict)]
                if isinstance(lessons, list)
                else []
            ),
            "hit_rate_over_time": [],
            "last_tuned_case_count": last_tuned_case_count,
        }

    def _rebuild_metrics(self) -> None:
        rules = {
            rule["id"]: {
                **rule,
                "samples": 0,
                "hits": 0,
                "misses": 0,
                "hit_rate": None,
                "expectancy": None,
                "expectancy_money": None,
                "capital_context": normalize_capital_context(
                    DEFAULT_CAPITAL_CONTEXT
                ),
                "baseline_hit_rate": None,
                "weight": 1.0,
                "flagged": False,
            }
            for rule in RULE_DEFINITIONS
        }
        rule_r_totals = {rule_id: 0.0 for rule_id in rules}
        hits = 0
        total_r = 0.0
        total_money = 0.0
        money_count = 0
        overall_capital_context = normalize_capital_context(DEFAULT_CAPITAL_CONTEXT)
        rule_money_totals = {rule_id: 0.0 for rule_id in rules}
        rule_money_counts = {rule_id: 0 for rule_id in rules}
        history: list[dict[str, Any]] = []
        lessons = list(self._state.get("lessons", []))
        case_count = len(self._cases)
        history_start = max(0, case_count - MAX_HISTORY_POINTS)
        for index, case in enumerate(self._cases):
            net_r = float(case["net_r"])
            is_hit = net_r > 0
            hits += int(is_hit)
            total_r += net_r
            net_usd = _finite_optional_float(case.get("net_usd"), "Case net USD")
            if net_usd is not None:
                total_money += net_usd
                money_count += 1
            case_capital_context = normalize_capital_context(
                case.get("capital_context"),
                symbol=str(case.get("symbol", "XAUUSD")),
            )
            overall_capital_context = case_capital_context
            processed = index + 1
            baseline = hits / processed
            for rule_id, metric in rules.items():
                if not matches_rule(rule_id, case):
                    continue
                metric["samples"] += 1
                metric["hits"] += int(is_hit)
                metric["misses"] += int(not is_hit)
                rule_r_totals[rule_id] += net_r
                metric["hit_rate"] = metric["hits"] / metric["samples"]
                metric["expectancy"] = (
                    rule_r_totals[rule_id] / metric["samples"]
                )
                metric["capital_context"] = case_capital_context
                if net_usd is not None:
                    rule_money_totals[rule_id] += net_usd
                    rule_money_counts[rule_id] += 1
                    metric["expectancy_money"] = (
                        rule_money_totals[rule_id] / rule_money_counts[rule_id]
                    )
            if index >= history_start:
                history.append(
                    {
                        "case_id": case["case_id"],
                        "at": case.get("resolved_at_utc") or _utc_now(),
                        "case_count": processed,
                        "hit_rate": baseline,
                        "expectancy": total_r / processed,
                        "expectancy_money": (
                            total_money / money_count if money_count else None
                        ),
                        "capital_context": case_capital_context,
                    }
                )

        overall_count = case_count
        baseline_hit_rate = hits / overall_count if overall_count else None
        for metric in rules.values():
            if metric["samples"] == 0:
                metric["baseline_hit_rate"] = baseline_hit_rate
            elif baseline_hit_rate is not None:
                metric["baseline_hit_rate"] = baseline_hit_rate
            metric["flagged"] = bool(
                metric["samples"] >= MIN_RULE_CASES
                and metric["hit_rate"] is not None
                and baseline_hit_rate is not None
                and metric["hit_rate"] < baseline_hit_rate
            )
            if (
                metric["samples"] >= MIN_RULE_CASES
                and baseline_hit_rate is not None
            ):
                metric["weight"] = max(
                    0.1,
                    min(
                        1.0,
                        1.0
                        - max(0.0, baseline_hit_rate - float(metric["hit_rate"]))
                        * 2.0,
                    ),
                )

        self._state.update(
            {
                "case_count": overall_count,
                "overall": {
                    "hits": hits,
                    "misses": overall_count - hits,
                    "hit_rate": baseline_hit_rate,
                    "expectancy": total_r / overall_count if overall_count else None,
                    "expectancy_money": (
                        total_money / money_count if money_count else None
                    ),
                    "capital_context": overall_capital_context,
                },
                "rules": list(rules.values()),
                "hit_rate_over_time": history[-MAX_HISTORY_POINTS:],
                "lessons": lessons[-MAX_LESSONS:],
                "informational_only": True,
            }
        )
        self._rule_r_totals = rule_r_totals
        self._overall_r_total = total_r
        self._overall_hits = hits
        self._overall_money_total = total_money
        self._overall_money_count = money_count
        self._rule_money_totals = rule_money_totals
        self._rule_money_counts = rule_money_counts

    def _persist_state(self) -> None:
        atomic_write_json(self.state_path, self._state)

    def _append_case(self, case: Mapping[str, Any]) -> None:
        self.cases_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            case,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        with self.cases_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")
            stream.flush()

    def ingest_cases(self, cases: Sequence[Mapping[str, Any]]) -> int:
        """Persist new resolved cases once and update beliefs without LLM calls."""
        added = 0
        with self._lock:
            for raw_case in cases:
                case = _json_safe(raw_case)
                if not isinstance(case, dict):
                    raise ValueError("A System Intelligence case must be an object.")
                if str(case.get("symbol", "")).upper() != "XAUUSD":
                    continue
                case_id = str(case.get("case_id", ""))
                if not case_id or case_id in self._case_ids:
                    continue
                if case.get("outcome") not in {"WIN", "LOSS"}:
                    raise ValueError("A System Intelligence case must be resolved.")
                net_r = case.get("net_r")
                if isinstance(net_r, bool) or not isinstance(net_r, (int, float)):
                    raise ValueError("A System Intelligence case requires finite net R.")
                if not math.isfinite(float(net_r)):
                    raise ValueError("A System Intelligence case requires finite net R.")
                self._append_case(case)
                self._cases.append(case)
                self._case_ids.add(case_id)
                self._apply_case(case)
                added += 1
            if added:
                self._maybe_adjust_tunables()
                self._persist_state()
        return added

    def _apply_case(self, case: Mapping[str, Any]) -> None:
        overall = self._state["overall"]
        count = int(self._state["case_count"]) + 1
        net_r = float(case["net_r"])
        net_usd = _finite_optional_float(case.get("net_usd"), "Case net USD")
        capital_context = normalize_capital_context(
            case.get("capital_context"), symbol=str(case.get("symbol", "XAUUSD"))
        )
        is_hit = net_r > 0
        self._overall_hits += int(is_hit)
        self._overall_r_total += net_r
        if net_usd is not None:
            self._overall_money_total += net_usd
            self._overall_money_count += 1
        baseline = self._overall_hits / count
        overall.update(
            {
                "hits": self._overall_hits,
                "misses": count - self._overall_hits,
                "hit_rate": baseline,
                "expectancy": self._overall_r_total / count,
                "expectancy_money": (
                    self._overall_money_total / self._overall_money_count
                    if self._overall_money_count
                    else None
                ),
                "capital_context": capital_context,
            }
        )
        self._state["case_count"] = count

        rules_by_id = {item["id"]: item for item in self._state["rules"]}
        for rule_id, metric in rules_by_id.items():
            rule_matched = matches_rule(rule_id, case)
            if rule_matched:
                metric["samples"] += 1
                metric["hits"] += int(is_hit)
                metric["misses"] += int(not is_hit)
                self._rule_r_totals[rule_id] += net_r
                metric["hit_rate"] = metric["hits"] / metric["samples"]
                metric["expectancy"] = (
                    self._rule_r_totals[rule_id] / metric["samples"]
                )
                metric["capital_context"] = capital_context
                if net_usd is not None:
                    self._rule_money_totals[rule_id] += net_usd
                    self._rule_money_counts[rule_id] += 1
                    metric["expectancy_money"] = (
                        self._rule_money_totals[rule_id]
                        / self._rule_money_counts[rule_id]
                    )
            metric["baseline_hit_rate"] = baseline
            old_weight = float(metric.get("weight", 1.0))
            rate = metric.get("hit_rate")
            flagged = (
                metric["samples"] >= MIN_RULE_CASES
                and rate is not None
                and rate < baseline
            )
            new_weight = (
                max(0.1, min(1.0, 1.0 - max(0.0, baseline - float(rate)) * 2.0))
                if metric["samples"] >= MIN_RULE_CASES and rate is not None
                else 1.0
            )
            metric["flagged"] = flagged
            metric["weight"] = new_weight
            if rule_matched or new_weight != old_weight:
                self._lesson(
                    rule_id=rule_id,
                    rule_name=metric["name"],
                    action=(
                        "Downweighted below-baseline rule."
                        if new_weight < old_weight
                        else (
                            "Recorded new evidence for the seeded rule."
                            if new_weight == old_weight
                            else "Adjusted rule weight as its relative evidence changed."
                        )
                    ),
                    evidence={
                        "case_id": case["case_id"],
                        "n": metric["samples"],
                        "hits": metric["hits"],
                        "misses": metric["misses"],
                        "hit_rate": metric["hit_rate"],
                        "baseline_hit_rate": baseline,
                        "expectancy": metric["expectancy"],
                        "expectancy_money": metric["expectancy_money"],
                        "capital_context": capital_context,
                        "window": count,
                        "previous_weight": old_weight,
                        "weight": new_weight,
                    },
                )

        history = self._state["hit_rate_over_time"]
        history.append(
            {
                "case_id": case["case_id"],
                "at": case.get("resolved_at_utc") or _utc_now(),
                "case_count": count,
                "hit_rate": baseline,
                "expectancy": self._overall_r_total / count,
                "expectancy_money": (
                    self._overall_money_total / self._overall_money_count
                    if self._overall_money_count
                    else None
                ),
                "capital_context": capital_context,
            }
        )
        del history[:-MAX_HISTORY_POINTS]

    def _lesson(
        self,
        *,
        rule_id: str,
        rule_name: str,
        action: str,
        evidence: Mapping[str, Any],
    ) -> None:
        lesson_basis = {
            "rule_id": rule_id,
            "action": action,
            "evidence": _json_safe(evidence),
        }
        lesson_id = hashlib.sha256(
            json.dumps(lesson_basis, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        lessons = self._state.setdefault("lessons", [])
        add_lesson_once(
            lessons,
            lesson_id=lesson_id,
            rule_id=rule_id,
            rule_name=rule_name,
            action=action,
            evidence=lesson_basis["evidence"],
            created_at_utc=_utc_now(),
            maximum=MAX_LESSONS,
        )

    def _maybe_adjust_tunables(self) -> None:
        count = len(self._cases)
        updated, next_tuned_count, changed = adjust_tunables(
            self._cases,
            self._state["tunables"],
            case_count=count,
            last_tuned_case_count=int(self._state.get("last_tuned_case_count", 0)),
        )
        previous = self._state["tunables"]
        if changed:
            self._lesson(
                rule_id="bounded_tunables",
                rule_name="Bounded parameter adjustment",
                action=f"Adjusted {', '.join(changed)} within the approved whitelist bounds.",
                evidence={
                    "n": count,
                    "expectancy": self._state["overall"]["expectancy"],
                    "expectancy_money": self._state["overall"][
                        "expectancy_money"
                    ],
                    "capital_context": self._state["overall"][
                        "capital_context"
                    ],
                    "window": min(count, 200),
                    "changed": changed,
                    "tunables": updated,
                },
            )
            self._state["tunables"] = updated
        self._state["last_tuned_case_count"] = next_tuned_count

    def scoring_snapshot(self) -> dict[str, Any]:
        """Freeze the current evidence for deterministic batch/session scoring."""
        with self._lock:
            return {
                "rules": _json_safe(self._state.get("rules", [])),
                "overall": _json_safe(self._state.get("overall", {})),
            }

    def score_alert(
        self,
        alert: Mapping[str, Any],
        formation_fingerprint: Mapping[str, Any],
        belief_state: Mapping[str, Any] | None = None,
    ) -> float:
        state = belief_state if belief_state is not None else self.scoring_snapshot()
        overall = state.get("overall", {})
        if not isinstance(overall, Mapping):
            overall = {}
        features = {
            "direction": _first(alert, ("direction",), ""),
            "cct": _first(alert, ("cct", "cct_state", "cctState"), ""),
            "cst_v2_state": _first(
                alert, ("cst_v2_state", "cstV2State", "cst"), ""
            ),
            "mtf_trend_agree": bool(
                _first(alert, ("mtf_trend_agree", "mtfTrendAgree"), False)
            ),
            "swing_reversal": bool(
                _first(alert, ("swing_reversal", "swingReversal"), False)
            ),
        }
        rules = state.get("rules", [])
        return score_belief_rules(
            {"case_type": "real", "features": features,
             "formation_fingerprint": formation_fingerprint},
            rules if isinstance(rules, list) else [],
            baseline_hit_rate=(
                float(overall["hit_rate"])
                if overall.get("hit_rate") is not None
                else None
            ),
            baseline_expectancy=(
                float(overall["expectancy"])
                if overall.get("expectancy") is not None
                else None
            ),
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return _json_safe(self._state)

    def dashboard_snapshot(self) -> dict[str, Any]:
        state = self.snapshot()
        overall = state.get("overall", {})
        as_percent = lambda value: (
            None if value is None else round(float(value) * 100, 1)
        )
        return {
            "symbol": "XAUUSD",
            "informationalOnly": True,
            "caseCount": int(state.get("case_count", 0)),
            "overall": {
                "hits": int(overall.get("hits", 0)),
                "misses": int(overall.get("misses", 0)),
                "hitRate": as_percent(overall.get("hit_rate")),
                "expectancy": overall.get("expectancy"),
                "expectancyMoney": overall.get("expectancy_money"),
                "capitalContext": overall.get("capital_context"),
            },
            "rules": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "description": item["description"],
                    "samples": item["samples"],
                    "hits": item["hits"],
                    "misses": item["misses"],
                    "hitRate": as_percent(item["hit_rate"]),
                    "expectancy": item["expectancy"],
                    "expectancyMoney": item.get("expectancy_money"),
                    "capitalContext": item.get("capital_context"),
                    "baselineHitRate": as_percent(item["baseline_hit_rate"]),
                    "weight": item["weight"],
                    "flagged": item["flagged"],
                }
                for item in state.get("rules", [])
            ],
            "hitRateOverTime": [
                {
                    "at": item["at"],
                    "caseCount": item["case_count"],
                    "hitRate": as_percent(item["hit_rate"]),
                    "expectancy": item["expectancy"],
                    "expectancyMoney": item.get("expectancy_money"),
                    "capitalContext": item.get("capital_context"),
                }
                for item in state.get("hit_rate_over_time", [])
            ],
            "lessons": [
                {
                    "id": item["lesson_id"],
                    "ruleId": item["rule_id"],
                    "ruleName": item["rule_name"],
                    "action": item["action"],
                    "evidence": item["evidence"],
                    "createdAtUtc": item["created_at_utc"],
                }
                for item in state.get("lessons", [])
            ],
            "tunables": {
                "strengthWeights": state["tunables"]["strength_weights"],
                "strengthThresholds": state["tunables"]["strength_thresholds"],
                "invertWeights": state["tunables"]["invert_weights"],
            },
        }

