"""Incremental, cached evaluation for interactive replay sessions.

The frozen engine exposes batch CST calculation, so this module keeps the same
state transitions in a session-local adapter and checks them against that
batch implementation in tests. Strategy rules in engine.py remain unchanged.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from . import backtest
from .alert_policy import (
    load_policy,
    policy_rejection_reasons,
    validate_policy,
)
from .alert_snapshots import (
    append_snapshot,
    build_alert_snapshot,
    trade_snapshot_updates,
)
from .engine import (
    CSTState,
    Candle,
    WickPressure,
    WICK_PRESSURE_BALANCE_THRESHOLD,
    WICK_PRESSURE_WINDOW,
    _breaks_level,
    _is_swing_high,
    _is_swing_low,
    _wick_contribution,
    _wick_pressure_from_sums,
    body_engulfs_previous,
    calculate_strength,
    classify_cct,
    detect_trigger,
    detect_trigger_b,
    smaller_body_note,
)
from .simulation import SimulationSession
from .shadow_detectors import (
    StreamingDirectionalChange,
    StreamingHierarchicalSwings,
    StreamingSimpleATR,
)

REPLAY_ADVANCE_CHUNK = 8192
MAX_VISIBLE_STATES = 1000
SESSION_TTL_SECONDS = 30 * 60
MTF_WINDOW = 6


class StreamingWickPressure:
    """Track normalized wick pressure over the latest closed candles."""

    def __init__(
        self,
        *,
        window_size: int = WICK_PRESSURE_WINDOW,
        balance_threshold: Decimal = WICK_PRESSURE_BALANCE_THRESHOLD,
    ):
        if window_size < 1:
            raise ValueError("window_size must be positive")
        if (
            not balance_threshold.is_finite()
            or balance_threshold < Decimal("0")
            or balance_threshold >= Decimal("1")
        ):
            raise ValueError("balance_threshold must be finite and within [0, 1)")
        self.window_size = window_size
        self.balance_threshold = balance_threshold
        self.contributions: deque[tuple[Decimal, Decimal]] = deque()
        self.lower_sum = Decimal("0")
        self.upper_sum = Decimal("0")

    def update(self, candle: Candle) -> WickPressure:
        lower, upper = _wick_contribution(candle)

        if len(self.contributions) >= self.window_size:
            old_lower, old_upper = self.contributions.popleft()
            self.lower_sum -= old_lower
            self.upper_sum -= old_upper
        self.contributions.append((lower, upper))
        self.lower_sum += lower
        self.upper_sum += upper

        return _wick_pressure_from_sums(
            self.lower_sum,
            self.upper_sum,
            self.balance_threshold,
        )


class StreamingCST:
    """The frozen compute_cst state machine, advanced by one closed candle."""

    def __init__(self, *, swing_neighbors: int, break_basis: str):
        if swing_neighbors < 1:
            raise ValueError("swing_neighbors must be positive")
        if break_basis not in {"close", "wick"}:
            raise ValueError("break_basis must be 'close' or 'wick'")
        self.swing_neighbors = swing_neighbors
        self.break_basis = break_basis
        self.window: deque[Candle] = deque(maxlen=2 * swing_neighbors + 1)
        self.index = -1
        self.state: CSTState = "undefined"
        self.old_trend: Literal["uptrend", "downtrend"] | None = None
        self.transition_start = -1
        self.resume_level: Decimal | None = None
        self.confirmation_level: Decimal | None = None
        self.swing_highs: list[tuple[int, Decimal]] = []
        self.swing_lows: list[tuple[int, Decimal]] = []
        self.last_confirmed_swings: list[tuple[str, int, Decimal]] = []

    def update(self, candle: Candle) -> CSTState:
        self.index += 1
        self.last_confirmed_swings = []
        self.window.append(candle)
        window = list(self.window)
        neighbors = self.swing_neighbors
        pivot_index = self.index - neighbors
        new_high: tuple[int, Decimal] | None = None
        new_low: tuple[int, Decimal] | None = None

        if pivot_index >= neighbors:
            local_pivot = len(window) - neighbors - 1
            if _is_swing_high(window, local_pivot, neighbors):
                new_high = (pivot_index, window[local_pivot].high)
                self.swing_highs.append(new_high)
                self.last_confirmed_swings.append(
                    ("high", pivot_index, window[local_pivot].high)
                )
            if _is_swing_low(window, local_pivot, neighbors):
                new_low = (pivot_index, window[local_pivot].low)
                self.swing_lows.append(new_low)
                self.last_confirmed_swings.append(
                    ("low", pivot_index, window[local_pivot].low)
                )

        last_high = self.swing_highs[-1][1] if self.swing_highs else None
        last_low = self.swing_lows[-1][1] if self.swing_lows else None
        initiated_transition = False

        if self.state == "undefined":
            broke_up = last_high is not None and _breaks_level(
                candle, last_high, "up", self.break_basis
            )
            broke_down = last_low is not None and _breaks_level(
                candle, last_low, "down", self.break_basis
            )
            if broke_up and not broke_down:
                self.state = "uptrend"
            elif broke_down and not broke_up:
                self.state = "downtrend"
        elif self.state == "uptrend" and last_low is not None:
            if _breaks_level(candle, last_low, "down", self.break_basis):
                self.state = "transition"
                self.old_trend = "uptrend"
                self.transition_start = self.index
                self.resume_level = last_high
                self.confirmation_level = None
                initiated_transition = True
        elif self.state == "downtrend" and last_high is not None:
            if _breaks_level(candle, last_high, "up", self.break_basis):
                self.state = "transition"
                self.old_trend = "downtrend"
                self.transition_start = self.index
                self.resume_level = last_low
                self.confirmation_level = None
                initiated_transition = True

        if self.state == "transition" and not initiated_transition:
            if (
                self.old_trend == "uptrend"
                and new_low
                and new_low[0] > self.transition_start
            ):
                self.confirmation_level = new_low[1]
            elif (
                self.old_trend == "downtrend"
                and new_high
                and new_high[0] > self.transition_start
            ):
                self.confirmation_level = new_high[1]

            if self.old_trend == "uptrend":
                resumes_old = (
                    self.resume_level is not None
                    and _breaks_level(
                        candle, self.resume_level, "up", self.break_basis
                    )
                )
                confirms_new = (
                    self.confirmation_level is not None
                    and _breaks_level(
                        candle,
                        self.confirmation_level,
                        "down",
                        self.break_basis,
                    )
                )
                if resumes_old and not confirms_new:
                    self.state = "uptrend"
                elif confirms_new and not resumes_old:
                    self.state = "downtrend"
            elif self.old_trend == "downtrend":
                resumes_old = (
                    self.resume_level is not None
                    and _breaks_level(
                        candle, self.resume_level, "down", self.break_basis
                    )
                )
                confirms_new = (
                    self.confirmation_level is not None
                    and _breaks_level(
                        candle,
                        self.confirmation_level,
                        "up",
                        self.break_basis,
                    )
                )
                if resumes_old and not confirms_new:
                    self.state = "downtrend"
                elif confirms_new and not resumes_old:
                    self.state = "uptrend"

            if self.state != "transition":
                self.old_trend = None
                self.transition_start = -1
                self.resume_level = None
                self.confirmation_level = None

        return self.state


def _swing_events_for_updates(
    *,
    index: int,
    candle: Candle,
    records: Sequence[Mapping[str, Any]],
    cct_window: Sequence[Candle],
    cct_neighbors: int,
    cst_swings: Sequence[tuple[str, int, Decimal]],
    dc_swing: Mapping[str, Any] | None,
    hier_promotions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Serialize only pivots confirmed by the currently closed candle."""
    confirmed_time = candle.time
    events: list[dict[str, Any]] = []

    def append_event(
        direction: str,
        pivot_index: int,
        price: Any,
        source: str,
        level: int | None = None,
    ) -> None:
        if direction not in {"high", "low"} or not 0 <= pivot_index < len(records):
            return
        event: dict[str, Any] = {
            "type": f"swing_{direction}",
            "pivot_time": str(records[pivot_index]["time"]),
            "confirmed_time": confirmed_time,
            "price": float(price),
            "source": source,
            "pivot_index": pivot_index,
            "confirmed_index": index,
        }
        if level is not None:
            event["level"] = level
        events.append(event)

    if len(cct_window) >= 2 * cct_neighbors + 1:
        local_pivot = len(cct_window) - cct_neighbors - 1
        pivot_index = index - cct_neighbors
        window = list(cct_window)
        if _is_swing_high(window, local_pivot, cct_neighbors):
            append_event("high", pivot_index, window[local_pivot].high, "cct_minor")
        if _is_swing_low(window, local_pivot, cct_neighbors):
            append_event("low", pivot_index, window[local_pivot].low, "cct_minor")

    for direction, pivot_index, price in cst_swings:
        append_event(direction, pivot_index, price, "cst_major")

    if dc_swing is not None:
        append_event(
            str(dc_swing.get("direction", "")),
            int(dc_swing.get("pivot_index", -1)),
            dc_swing.get("price"),
            "dc",
        )

    for promotion in hier_promotions:
        direction = str(promotion.get("direction", ""))
        pivot_index = int(promotion.get("pivot_index", -1))
        if 0 <= pivot_index < len(records) and direction in {"high", "low"}:
            price_key = "high" if direction == "high" else "low"
            append_event(
                direction,
                pivot_index,
                records[pivot_index][price_key],
                "hier",
                int(promotion["depth"]),
            )
    return events


def compute_confirmed_swing_events(
    records: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Compute the complete causal swing set for a static candle window."""
    if not records:
        return []
    cct_settings = spec["cct"]
    cst_settings = spec["cst"]
    cct_neighbors = int(cct_settings["swing_neighbors"])
    cct_window: deque[Candle] = deque(maxlen=int(cct_settings["window"]))
    cst = StreamingCST(
        swing_neighbors=int(cst_settings["major_swing_neighbors"]),
        break_basis=str(cst_settings["break_basis"]),
    )
    atr = StreamingSimpleATR()
    dc = StreamingDirectionalChange()
    hier = StreamingHierarchicalSwings()
    events: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        candle = Candle.from_values(
            str(record["time"]),
            record["open"],
            record["high"],
            record["low"],
            record["close"],
        )
        cct_window.append(candle)
        cst.update(candle)
        atr_value = atr.update(candle)
        dc.update(candle, atr_value)
        _hier_state, promotions = hier.update(candle)
        events.extend(
            _swing_events_for_updates(
                index=index,
                candle=candle,
                records=records,
                cct_window=list(cct_window),
                cct_neighbors=cct_neighbors,
                cst_swings=cst.last_confirmed_swings,
                dc_swing=dc.last_confirmed_swing,
                hier_promotions=promotions,
            )
        )
    return events


def _bar_close_time(record: Mapping[str, Any], minutes: int) -> datetime:
    timestamp = datetime.fromisoformat(str(record["time"]).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc) + timedelta(minutes=minutes)


def _empty_mtf_context() -> dict[str, Any]:
    return {
        "mtf_cst": None,
        "mtf_cct": None,
        "mtf_wick_pressure": None,
        "mtf_closed_at": None,
        "dual_tf_directions": {"BUY": False, "SELL": False},
    }


def build_mtf_context(
    m5_records: Sequence[Mapping[str, Any]],
    m15_records: Sequence[Mapping[str, Any]],
    *,
    spec: Mapping[str, Any],
    cct_window: int | None = None,
    m15_alerts_by_index: Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Map only already-closed M15 states and alerts onto each M5 candle."""
    if not m5_records:
        return []
    if not m15_records:
        return [_empty_mtf_context() for _ in m5_records]

    cct_settings = spec["cct"]
    cst_settings = spec["cst"]
    window_size = int(cct_window or cct_settings["window"])
    neighbors = int(cct_settings["swing_neighbors"])
    cct_window_records: deque[Candle] = deque(maxlen=window_size)
    cst = StreamingCST(
        swing_neighbors=int(cst_settings["major_swing_neighbors"]),
        break_basis=str(cst_settings["break_basis"]),
    )
    pressure = StreamingWickPressure()
    m15_states: list[dict[str, str]] = []
    m15_close_times: list[datetime] = []
    m15_candles = [
        Candle.from_values(
            str(record["time"]),
            record["open"],
            record["high"],
            record["low"],
            record["close"],
        )
        for record in m15_records
    ]
    for index, (record, candle) in enumerate(zip(m15_records, m15_candles)):
        cct_window_records.append(candle)
        m15_states.append(
            {
                "cst": str(cst.update(candle)),
                "cct": str(
                    classify_cct(
                        list(cct_window_records),
                        window=window_size,
                        swing_neighbors=neighbors,
                    )
                ),
                "wick_pressure": str(pressure.update(candle)),
            }
        )
        m15_close_times.append(_bar_close_time(record, 15))

    events: list[tuple[datetime, str]] = []
    if m15_alerts_by_index is None:
        for index in range(1, len(m15_candles)):
            previous = m15_candles[index - 1]
            current = m15_candles[index]
            direction = detect_trigger(previous, current)
            if direction is not None:
                events.append((m15_close_times[index], str(direction)))
            variant_type = detect_trigger_b(previous, current)
            if variant_type is not None:
                variant_direction = (
                    "SELL" if str(variant_type) == "SELL-B" else "BUY"
                )
                events.append((m15_close_times[index], variant_direction))
    else:
        for index, alerts in m15_alerts_by_index.items():
            if not 0 <= int(index) < len(m15_close_times):
                continue
            for alert in alerts:
                direction = str(alert.get("direction", "")).upper()
                if direction in {"BUY", "SELL"}:
                    events.append((m15_close_times[int(index)], direction))
    events.sort(key=lambda event: event[0])

    contexts: list[dict[str, Any]] = []
    state_index = -1
    event_index = 0
    recent_events: deque[tuple[datetime, str]] = deque()
    window_duration = timedelta(minutes=5 * MTF_WINDOW)
    for record in m5_records:
        m5_close_time = _bar_close_time(record, 5)
        while (
            state_index + 1 < len(m15_close_times)
            and m15_close_times[state_index + 1] < m5_close_time
        ):
            state_index += 1

        while event_index < len(events) and events[event_index][0] < m5_close_time:
            recent_events.append(events[event_index])
            event_index += 1
        window_start = m5_close_time - window_duration
        while recent_events and recent_events[0][0] < window_start:
            recent_events.popleft()

        if state_index < 0:
            context = _empty_mtf_context()
        else:
            state = m15_states[state_index]
            context = {
                "mtf_cst": state["cst"],
                "mtf_cct": state["cct"],
                "mtf_wick_pressure": state["wick_pressure"],
                "mtf_closed_at": m15_close_times[state_index]
                .isoformat()
                .replace("+00:00", "Z"),
                "dual_tf_directions": {"BUY": False, "SELL": False},
            }
        context["dual_tf_directions"] = {
            direction: any(event_direction == direction for _, event_direction in recent_events)
            for direction in ("BUY", "SELL")
        }
        contexts.append(context)
    return contexts


class ReplaySession:
    """Session-scoped evaluator; computed states and alerts are never replayed."""

    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        timeframe: str,
        from_value: str,
        to_value: str,
        records: list[dict[str, Any]],
        source: str,
        source_sha256: str,
        spec: dict[str, Any],
        settings_fingerprint: str,
        simulation_settings: Mapping[str, Any] | None = None,
        alert_policy: Mapping[str, Any] | None = None,
        mtf_records: list[dict[str, Any]] | None = None,
        snapshot_path: Path | None = None,
    ):
        if not records:
            raise ValueError("A replay session requires at least one candle.")
        self.session_id = session_id
        self.run_id = run_id
        self.timeframe = timeframe
        self.from_value = from_value
        self.to_value = to_value
        self.records = records
        self.source = source
        self.source_sha256 = source_sha256
        self.spec = spec
        instruments = spec.get("instruments", ["XAUUSD"])
        self.symbol = str(instruments[0]) if instruments else "XAUUSD"
        self.settings_fingerprint = settings_fingerprint
        self.snapshot_path = Path(snapshot_path) if snapshot_path is not None else None
        self.mtf_context = build_mtf_context(
            records,
            mtf_records or [],
            spec=spec,
        ) if timeframe == "M5" else [_empty_mtf_context() for _ in records]
        self._pending_snapshots: dict[str, dict[str, Any]] = {}
        self.alert_policy = validate_policy(
            alert_policy
            if alert_policy is not None
            else {
                "schema_version": 1,
                "version": 1,
                "mode": "passthrough",
                "settings": {
                    "min_strength": "NONE",
                    "require_wp_agree": False,
                    "require_engulf": False,
                    "timeframes": ["M5", "M15"],
                    "exit_model": "swept_level_atr",
                    "cooldown_candles": 0,
                },
                "promoted_at_utc": None,
                "source_champion_version": None,
            }
        )
        self.simulation = SimulationSession(
            records,
            symbol=self.symbol,
            settings=simulation_settings,
        )
        self.policy_simulation: SimulationSession | None = None
        if self.alert_policy["mode"] == "active":
            policy_simulation_settings = dict(simulation_settings or {})
            policy_simulation_settings["exitModel"] = self.alert_policy["settings"][
                "exit_model"
            ]
            self.policy_simulation = SimulationSession(
                records,
                symbol=self.symbol,
                settings=policy_simulation_settings,
            )
        self.policy_decisions: list[dict[str, Any]] = []
        self._policy_last_allowed_index: int | None = None
        self.cct_settings = spec["cct"]
        self.cst_settings = spec["cst"]
        self.cct_window_size = int(self.cct_settings["window"])
        self.cct_neighbors = int(self.cct_settings["swing_neighbors"])
        self.cct_window: deque[Candle] = deque(maxlen=self.cct_window_size)
        self.cst = StreamingCST(
            swing_neighbors=int(self.cst_settings["major_swing_neighbors"]),
            break_basis=str(self.cst_settings["break_basis"]),
        )
        self.wick_pressure = StreamingWickPressure()
        self.states: list[dict[str, Any]] = []
        self.directional_change = StreamingDirectionalChange()
        self.hierarchical_swings = StreamingHierarchicalSwings()
        self.wick_pressure_states: list[WickPressure] = []
        self.alerts: list[tuple[int, dict[str, Any]]] = []
        self.swing_events: list[dict[str, Any]] = []
        self.computed_through = -1
        self.lock = threading.RLock()
        self.last_access = time.monotonic()

    @property
    def total_candles(self) -> int:
        return len(self.records)

    def initial_response(self) -> dict[str, Any]:
        with self.lock:
            self._process_through(0, simulation_filters=None)
            self.last_access = time.monotonic()
            return {
                "sessionId": self.session_id,
                "runId": self.run_id,
                "timeframe": self.timeframe,
                "from": self.from_value,
                "to": self.to_value,
                "totalCandles": self.total_candles,
                "cursor": 0,
                "computedThrough": self.computed_through,
                "states": [self._state_at(0)],
                "wickPressureStates": [self._wick_pressure_at(0)],
                "swingEvents": [],
                "simulation": self.simulation.snapshot(0),
                "policyVersion": self.alert_policy["version"],
                "policyDecisions": [],
                "policySimulation": (
                    self.policy_simulation.snapshot(0)
                    if self.policy_simulation is not None
                    else None
                ),
                "policyTrades": (
                    self.policy_simulation.trades_at(0)
                    if self.policy_simulation is not None
                    else []
                ),
            }

    def advance(
        self,
        *,
        to_index: int,
        view_start: int,
        view_end: int,
        simulation_filters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            if to_index < 0 or to_index >= self.total_candles:
                raise ValueError("Replay cursor is outside the selected candle window.")
            if (
                view_start < 0
                or view_end < view_start
                or view_end >= self.total_candles
                or view_end - view_start + 1 > MAX_VISIBLE_STATES
            ):
                raise ValueError(
                    f"Visible state window must contain 1–{MAX_VISIBLE_STATES} candles."
                )

            computed_from = self.computed_through + 1
            previous_alert_count = len(self.alerts)
            previous_policy_decision_count = len(self.policy_decisions)
            previous_swing_count = len(self.swing_events)
            opened_from, resolved_from = self.simulation.event_counts()
            process_through = min(
                to_index,
                self.computed_through + REPLAY_ADVANCE_CHUNK,
            )
            self._process_through(process_through, simulation_filters)
            self.last_access = time.monotonic()

            new_states = [
                self._state_at(index)
                for index in range(computed_from, self.computed_through + 1)
            ]
            visible_end = min(view_end, self.computed_through)
            visible_states = (
                [
                    self._state_at(index)
                    for index in range(view_start, visible_end + 1)
                ]
                if visible_end >= view_start
                else []
            )
            new_wick_pressure_states = [
                self._wick_pressure_at(index)
                for index in range(computed_from, self.computed_through + 1)
            ]
            visible_wick_pressure_states = (
                [
                    self._wick_pressure_at(index)
                    for index in range(view_start, visible_end + 1)
                ]
                if visible_end >= view_start
                else []
            )
            newly_opened, newly_resolved = self.simulation.event_deltas(
                opened_from, resolved_from
            )
            for trade in newly_opened:
                self._apply_snapshot_trade(trade, resolved=False)
            for trade in newly_resolved:
                self._apply_snapshot_trade(trade, resolved=True)
            if self.computed_through >= self.total_candles - 1:
                self.flush_snapshots()
            return {
                "sessionId": self.session_id,
                "runId": self.run_id,
                "timeframe": self.timeframe,
                "cursor": to_index,
                "requestedIndex": to_index,
                "computedFrom": computed_from if new_states else None,
                "computedThrough": self.computed_through,
                "totalCandles": self.total_candles,
                "hasMore": self.computed_through < to_index,
                "newStates": new_states,
                "visibleStates": visible_states,
                "newWickPressureStates": new_wick_pressure_states,
                "visibleWickPressureStates": visible_wick_pressure_states,
                "newSwingEvents": self.swing_events[previous_swing_count:],
                "newAlerts": [
                    {"candleIndex": index, "alert": alert}
                    for index, alert in self.alerts[previous_alert_count:]
                ],
                "policyVersion": self.alert_policy["version"],
                "policyDecisions": self.policy_decisions[
                    previous_policy_decision_count:
                ],
                "policySimulation": (
                    self.policy_simulation.snapshot(
                        min(to_index, self.computed_through)
                    )
                    if self.policy_simulation is not None
                    else None
                ),
                "policyTrades": (
                    self.policy_simulation.trades_at(
                        min(to_index, self.computed_through)
                    )
                    if self.policy_simulation is not None
                    else []
                ),
                "newlyOpenedTrades": newly_opened,
                "newlyResolvedTrades": newly_resolved,
                "simulation": self.simulation.snapshot(
                    min(to_index, self.computed_through)
                ),
            }

    def _state_at(self, index: int) -> dict[str, Any]:
        state = self.states[index]
        return {
            "index": index,
            "cct": state["cct"],
            "cst": state["cst"],
            "dcState": state["dcState"],
            "hierState": state["hierState"],
            "mtfCst": state["mtfCst"],
            "mtfCct": state["mtfCct"],
            "mtfWickPressure": state["mtfWickPressure"],
            "mtfClosedAt": state["mtfClosedAt"],
        }

    def _wick_pressure_at(self, index: int) -> dict[str, Any]:
        return {"index": index, "state": self.wick_pressure_states[index]}

    def _to_candle(self, record: dict[str, Any]) -> Candle:
        return Candle.from_values(
            str(record["time"]),
            record["open"],
            record["high"],
            record["low"],
            record["close"],
        )

    def _process_through(
        self,
        target: int,
        simulation_filters: Mapping[str, Any] | None,
    ) -> None:
        target = min(target, self.total_candles - 1)
        while self.computed_through < target:
            index = self.computed_through + 1
            record = self.records[index]
            self.simulation.process_candle(index)
            if self.policy_simulation is not None:
                self.policy_simulation.process_candle(index)
            candle = self._to_candle(record)
            self.cct_window.append(candle)
            cct = classify_cct(
                list(self.cct_window),
                window=self.cct_window_size,
                swing_neighbors=self.cct_neighbors,
            )
            cst = self.cst.update(candle)
            wick_pressure = self.wick_pressure.update(candle)
            dc_state = self.directional_change.update(
                candle, self.simulation.atr_at(index)
            )
            hier_state, hier_promotions = self.hierarchical_swings.update(candle)
            self.swing_events.extend(
                _swing_events_for_updates(
                    index=index,
                    candle=candle,
                    records=self.records,
                    cct_window=list(self.cct_window),
                    cct_neighbors=self.cct_neighbors,
                    cst_swings=self.cst.last_confirmed_swings,
                    dc_swing=self.directional_change.last_confirmed_swing,
                    hier_promotions=hier_promotions,
                )
            )
            mtf = self.mtf_context[index]
            self.states.append(
                {
                    "cct": str(cct),
                    "cst": str(cst),
                    "dcState": str(dc_state),
                    "hierState": str(hier_state),
                    "hierPromotions": hier_promotions,
                    "mtfCst": mtf["mtf_cst"],
                    "mtfCct": mtf["mtf_cct"],
                    "mtfWickPressure": mtf["mtf_wick_pressure"],
                    "mtfClosedAt": mtf["mtf_closed_at"],
                }
            )
            self.wick_pressure_states.append(wick_pressure)

            if index > 0:
                previous = self._to_candle(self.records[index - 1])
                direction = detect_trigger(previous, candle)
                typed_directions: list[tuple[str, str]] = []
                if direction is not None:
                    typed_directions.append((direction, f"{direction}-A"))
                variant_b_type = detect_trigger_b(previous, candle)
                if variant_b_type is not None:
                    variant_b_direction = (
                        "SELL" if variant_b_type == "SELL-B" else "BUY"
                    )
                    typed_directions.append((variant_b_direction, variant_b_type))

                for alert_direction, alert_type in typed_directions:
                    mtf_cst = mtf["mtf_cst"]
                    mtf_cct = mtf["mtf_cct"]
                    mtf_wp = mtf["mtf_wick_pressure"]
                    mtf_cst_agree = mtf_cst == (
                        "uptrend" if alert_direction == "BUY" else "downtrend"
                    )
                    mtf_cct_agree = mtf_cct == (
                        "uptrend" if alert_direction == "BUY" else "downtrend"
                    )
                    mtf_wp_agree = mtf_wp == (
                        "up-pressure" if alert_direction == "BUY" else "down-pressure"
                    )
                    dual_tf_alert = bool(
                        mtf["dual_tf_directions"].get(alert_direction, False)
                    )
                    engulfing = body_engulfs_previous(
                        previous, candle, alert_direction
                    )
                    strength_score, strength_label, strength_features = calculate_strength(
                        alert_direction,
                        cct=str(cct),
                        cst=str(cst),
                        wick_pressure=str(wick_pressure),
                        engulfing=engulfing,
                        mtf_cst=mtf_cst,
                        mtf_wick_pressure=mtf_wp,
                        dual_tf_alert=dual_tf_alert,
                    )
                    identity = {
                        "input_sha256": self.source_sha256,
                        "symbol": self.symbol,
                        "timeframe": self.timeframe,
                        "time": candle.time,
                        "direction": alert_direction,
                    }
                    if alert_type.endswith("-B"):
                        identity["alert_type"] = alert_type
                    alert = {
                        "alert_id": hashlib.sha256(
                            backtest.canonical_json(identity).encode("utf-8")
                        ).hexdigest(),
                        "time": candle.time,
                        "symbol": self.symbol,
                        "tf": self.timeframe,
                        "direction": alert_direction,
                        "alert_type": alert_type,
                        "cct": str(cct),
                        "cst": str(cst),
                        "dc_state": str(dc_state),
                        "hier_state": str(hier_state),
                        "hier_promotions": hier_promotions,
                        "mtf_cst": mtf_cst,
                        "mtf_cct": mtf_cct,
                        "mtf_wick_pressure": mtf_wp,
                        "mtf_closed_at": mtf["mtf_closed_at"],
                        "mtf_trend_agree": mtf_cst_agree,
                        "mtf_cct_agree": mtf_cct_agree,
                        "mtf_wp_agree": mtf_wp_agree,
                        "dual_tf_alert": dual_tf_alert,
                        "wick_pressure": str(wick_pressure),
                        "engulfing": engulfing,
                        "strength_score": str(strength_score),
                        "strength_features": strength_features,
                        "strength_label": strength_label,
                        "note": smaller_body_note(previous, candle) or "",
                        "period": "SMOKE",
                        "reward_score": "",
                        "reward_status": "NOT_SCORED",
                        "spec_version": str(self.spec["version"]),
                        "spec_status": str(self.spec.get("status", "unspecified")),
                        "settings_fingerprint": self.settings_fingerprint,
                        "cst_settings_json": backtest.canonical_json(self.spec["cst"]),
                        "reward_settings_json": backtest.canonical_json(
                            self.spec["reward"]
                        ),
                        "input_source": self.source,
                        "input_sha256": self.source_sha256,
                        "run_id": self.run_id,
                        "candle": record,
                        "previous_candle": self.records[index - 1],
                        "scored": False,
                    }
                    self.alerts.append((index, alert))
                    engulfing = body_engulfs_previous(
                        previous, candle, alert_direction  # type: ignore[arg-type]
                    )
                    policy_alert = {**alert, "engulfing": engulfing}
                    rejection_reasons = policy_rejection_reasons(
                        self.alert_policy,
                        policy_alert,
                        timeframe=self.timeframe,
                        candle_index=index,
                        wick_pressure=str(wick_pressure),
                        previous_allowed_index=self._policy_last_allowed_index,
                    )
                    passed_policy = not rejection_reasons
                    self.policy_decisions.append(
                        {
                            "alertId": alert["alert_id"],
                            "candleIndex": index,
                            "passed": passed_policy,
                            "reasons": rejection_reasons,
                        }
                    )
                    if passed_policy:
                        self._policy_last_allowed_index = index
                        if self.policy_simulation is not None:
                            self.policy_simulation.queue_alert(
                                index,
                                policy_alert,
                                simulation_filters,
                            )
                    queued = self.simulation.queue_alert(
                        index,
                        alert,
                        simulation_filters,
                    )
                    if self.snapshot_path is not None:
                        snapshot = build_alert_snapshot(
                            alert,
                            atr14=self.simulation.atr_at(index),
                            wp_state=wick_pressure,
                        )
                        alert_id = str(alert["alert_id"])
                        if queued:
                            self._pending_snapshots[alert_id] = snapshot
                        else:
                            append_snapshot(self.snapshot_path, snapshot)
            self.computed_through = index

    def _apply_snapshot_trade(
        self,
        trade: Mapping[str, Any],
        *,
        resolved: bool,
    ) -> None:
        alert_id = str(trade.get("alertId", ""))
        snapshot = self._pending_snapshots.get(alert_id)
        if snapshot is None:
            return
        snapshot.update(trade_snapshot_updates(trade))
        if resolved:
            self._persist_snapshot(alert_id)

    def _persist_snapshot(self, alert_id: str) -> None:
        snapshot = self._pending_snapshots.pop(alert_id, None)
        if snapshot is not None and self.snapshot_path is not None:
            append_snapshot(self.snapshot_path, snapshot)

    def flush_snapshots(self) -> None:
        """Persist alerts still open or queued when a replay session ends."""
        with self.lock:
            for alert_id in list(self._pending_snapshots):
                self._persist_snapshot(alert_id)
