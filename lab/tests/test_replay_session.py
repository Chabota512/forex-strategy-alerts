import hashlib
import json
import math
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from lab import backtest
from lab.alert_policy import passthrough_policy
from lab.alert_snapshots import append_batch_snapshots, read_snapshots
from lab.candle_data import CandleFileData
from lab.engine import Candle, WICK_PRESSURE_WINDOW, classify_cct, compute_cst
from lab.replay import (
    MAX_VISIBLE_STATES,
    ReplaySession,
    StreamingWickPressure,
    compute_confirmed_swing_events,
)
from lab.shadow_detectors import compute_shadow_states
from lab.simulation import simulate_batch

SPEC_PATH = Path(__file__).parents[1] / "specs" / "seed_v1.5.0.json"
FROM_DATE = date(2025, 1, 1)
TO_DATE = date(2025, 1, 10)
SOURCE_SHA256 = hashlib.sha256(b"10-day deterministic replay fixture").hexdigest()


def ten_day_m1_fixture() -> list[dict]:
    """Create ten UTC calendar days of deterministic candles with trigger events."""
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    records: list[dict] = []
    opened = Decimal("2600")

    for index in range(10 * 24 * 60):
        rising = index % 2 == 0
        closed = opened + (Decimal("0.08") if rising else Decimal("-0.08"))
        high = max(opened, closed) + Decimal("0.12")
        low = min(opened, closed) - Decimal("0.12")

        if records and index % 16 == 1:
            high = max(high, records[-1]["high"] + Decimal("0.01"))
        if records and index % 16 == 3:
            low = min(low, records[-1]["low"] - Decimal("0.01"))

        timestamp = (
            start + timedelta(minutes=index)
        ).isoformat().replace("+00:00", "Z")
        records.append(
            {
                "time": timestamp,
                "open": opened,
                "high": high,
                "low": low,
                "close": closed,
            }
        )
        opened = closed
    return records


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def swing_wave_fixture(count: int = 50) -> list[dict]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    records: list[dict] = []
    for index in range(count):
        amplitude = 10 + ((index // 12) % 3) * 2
        close = 100 + amplitude * math.sin(2 * math.pi * index / 12)
        price = Decimal(str(round(close, 4)))
        records.append(
            {
                "time": (start + timedelta(minutes=index))
                .isoformat()
                .replace("+00:00", "Z"),
                "open": price,
                "high": price + Decimal("0.2"),
                "low": price - Decimal("0.2"),
                "close": price,
            }
        )
    return records


class StreamingWickPressureTests(unittest.TestCase):
    def make_candle(self, open_: str, high: str, low: str, close: str) -> Candle:
        return Candle.from_values(
            "2025-01-01T00:00:00Z", open_, high, low, close
        )

    def test_classifies_lower_upper_and_balanced_normalized_wicks(self):
        self.assertEqual(
            StreamingWickPressure().update(
                self.make_candle("10", "12", "7", "11")
            ),
            "up-pressure",
        )
        self.assertEqual(
            StreamingWickPressure().update(
                self.make_candle("11", "14", "9", "10")
            ),
            "down-pressure",
        )
        self.assertEqual(
            StreamingWickPressure().update(
                self.make_candle("10", "13", "8", "11")
            ),
            "balanced",
        )

    def test_zero_range_candle_is_a_safe_balanced_observation(self):
        self.assertEqual(
            StreamingWickPressure().update(
                self.make_candle("10", "10", "10", "10")
            ),
            "balanced",
        )

    def test_ten_percent_relative_difference_is_still_balanced(self):
        self.assertEqual(
            StreamingWickPressure().update(
                self.make_candle("46", "101", "1", "46")
            ),
            "balanced",
        )

    def test_only_the_latest_closed_candles_contribute(self):
        pressure = StreamingWickPressure()
        lower_dominant = self.make_candle("10", "12", "7", "11")
        upper_dominant = self.make_candle("11", "14", "9", "10")

        for _ in range(WICK_PRESSURE_WINDOW):
            state = pressure.update(lower_dominant)
        self.assertEqual(state, "up-pressure")
        for _ in range(WICK_PRESSURE_WINDOW):
            state = pressure.update(upper_dominant)
        self.assertEqual(state, "down-pressure")


class ReplayFeatureStreamingTests(unittest.TestCase):
    def make_session(self, *, variant_b: bool) -> ReplaySession:
        records = [
            {
                "time": "2025-01-01T00:00:00Z",
                "open": Decimal("11"),
                "high": Decimal("12"),
                "low": Decimal("9"),
                "close": Decimal("10"),
            },
            {
                "time": "2025-01-01T00:05:00Z",
                "open": Decimal("10"),
                "high": Decimal("12.5"),
                "low": Decimal("9.2"),
                "close": Decimal("9.5"),
            },
        ]
        spec = backtest.load_spec(SPEC_PATH)
        return ReplaySession(
            session_id="variant-b-test",
            run_id="variant-b-run",
            timeframe="M5",
            from_value="2025-01-01",
            to_value="2025-01-01",
            records=records,
            source="test",
            source_sha256=SOURCE_SHA256,
            spec=spec,
            settings_fingerprint="test-settings",
            variant_b=variant_b,
        )

    def test_variant_b_is_opt_in_and_pressure_stream_is_additive(self):
        disabled = self.make_session(variant_b=False)
        disabled_start = disabled.initial_response()
        disabled_advance = disabled.advance(to_index=1, view_start=0, view_end=1)

        enabled = self.make_session(variant_b=True)
        enabled_start = enabled.initial_response()
        enabled_advance = enabled.advance(to_index=1, view_start=0, view_end=1)

        self.assertNotIn("wickPressure", disabled_start["states"][0])
        self.assertEqual(
            set(disabled_advance["newStates"][0]),
            {
                "index",
                "cct",
                "cst",
                "dcState",
                "hierState",
                "mtfCst",
                "mtfCct",
                "mtfWickPressure",
                "mtfClosedAt",
            },
        )
        self.assertEqual(
            enabled_start["wickPressureStates"],
            [{"index": 0, "state": "balanced"}],
        )
        self.assertEqual(
            enabled_advance["newWickPressureStates"],
            [{"index": 1, "state": "down-pressure"}],
        )
        self.assertEqual(
            [item["alert"]["alert_type"] for item in disabled_advance["newAlerts"]],
            [],
        )
        self.assertEqual(
            [item["alert"]["alert_type"] for item in enabled_advance["newAlerts"]],
            ["SELL-B"],
        )

    def test_month_boundary_advances_one_session_and_keeps_simulation_state(self):
        records = [
            {
                "time": "2023-01-31T23:45:00Z",
                "open": Decimal("2600"),
                "high": Decimal("2601"),
                "low": Decimal("2599"),
                "close": Decimal("2600.5"),
            },
            {
                "time": "2023-02-01T00:00:00Z",
                "open": Decimal("2600.5"),
                "high": Decimal("2602"),
                "low": Decimal("2600"),
                "close": Decimal("2601.5"),
            },
        ]
        session = ReplaySession(
            session_id="month-boundary",
            run_id="month-boundary-run",
            timeframe="M15",
            from_value="2023-01-31",
            to_value="2023-02-01",
            records=records,
            source="monthly-fixture",
            source_sha256=SOURCE_SHA256,
            spec=backtest.load_spec(SPEC_PATH),
            settings_fingerprint="month-boundary-fixture",
            simulation_settings={"enabled": True},
        )
        simulation_state = session.simulation
        started = session.initial_response()
        advanced = session.advance(to_index=1, view_start=0, view_end=1)

        self.assertEqual(started["computedThrough"], 0)
        self.assertEqual(advanced["computedThrough"], 1)
        self.assertEqual(advanced["cursor"], 1)
        self.assertEqual(
            session.records[advanced["cursor"]]["time"],
            "2023-02-01T00:00:00Z",
        )
        self.assertEqual([state["index"] for state in advanced["newStates"]], [1])
        self.assertIs(session.simulation, simulation_state)
        self.assertEqual(session.simulation.snapshot(1), advanced["simulation"])


class ReplaySessionParityTests(unittest.TestCase):
    def test_active_policy_filters_notifications_not_raw_alert_archives(self):
        records = ten_day_m1_fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        snapshot_path = Path(temporary.name) / "snapshots.jsonl"
        timeframe = "M1"
        effective_spec = {
            **backtest.load_spec(SPEC_PATH),
            "instruments": ["XAUUSD"],
            "timeframes": [timeframe],
        }
        policy = passthrough_policy()
        policy["mode"] = "active"
        policy["settings"]["timeframes"] = ["M5", "M15"]
        session = ReplaySession(
            session_id="policy-archive",
            run_id="policy-archive-run",
            timeframe=timeframe,
            from_value=FROM_DATE.isoformat(),
            to_value=TO_DATE.isoformat(),
            records=records,
            source="deterministic-test-fixture",
            source_sha256=SOURCE_SHA256,
            spec=effective_spec,
            settings_fingerprint="policy-archive-fixture",
            alert_policy=policy,
            snapshot_path=snapshot_path,
        )

        session.initial_response()
        target = len(records) - 1
        response = session.advance(
            to_index=target,
            view_start=max(0, target - MAX_VISIBLE_STATES + 1),
            view_end=target,
        )
        raw_alerts = list(response["newAlerts"])
        decisions = list(response["policyDecisions"])
        while response["hasMore"]:
            response = session.advance(
                to_index=target,
                view_start=max(0, target - MAX_VISIBLE_STATES + 1),
                view_end=target,
            )
            raw_alerts.extend(response["newAlerts"])
            decisions.extend(response["policyDecisions"])

        session.flush_snapshots()
        archived_alerts = read_snapshots(snapshot_path)

        self.assertGreater(len(raw_alerts), 0)
        self.assertEqual(len(decisions), len(raw_alerts))
        self.assertTrue(all(decision["passed"] is False for decision in decisions))
        self.assertEqual(
            len(archived_alerts),
            len(raw_alerts),
            "The raw snapshot archive must include alerts rejected by notification policy.",
        )

    def test_ten_day_batch_and_streaming_replays_are_identical(self):
        records = ten_day_m1_fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        batch_snapshot_path = Path(temporary.name) / "batch" / "snapshots.jsonl"
        session_snapshot_path = Path(temporary.name) / "session" / "snapshots.jsonl"
        timeframe = "M1"
        source = "deterministic-test-fixture"
        effective_spec = {
            **backtest.load_spec(SPEC_PATH),
            "instruments": ["XAUUSD"],
            "timeframes": [timeframe],
        }
        source_data = CandleFileData(
            candles=records,
            path=Path("fixture-XAUUSD-M1.csv"),
            source=source,
            sha256=SOURCE_SHA256,
        )
        batch = backtest.run_smoke_window(
            loaded={("XAUUSD", timeframe): source_data},
            spec=effective_spec,
            from_date=FROM_DATE,
            to_date=TO_DATE,
        )
        simulation_settings = {
            "enabled": True,
            "startingBalance": 10000,
            "riskPercent": 1,
            "rewardMultiple": 2,
        }
        session = ReplaySession(
            session_id="ten-day-parity",
            run_id=batch["run_id"],
            timeframe=timeframe,
            from_value=FROM_DATE.isoformat(),
            to_value=TO_DATE.isoformat(),
            records=records,
            source=source,
            source_sha256=SOURCE_SHA256,
            spec=effective_spec,
            settings_fingerprint=batch["settings_fingerprint"],
            simulation_settings=simulation_settings,
            snapshot_path=session_snapshot_path,
        )

        started = session.initial_response()
        streamed_states = list(started["states"])
        streamed_alerts: list[dict] = []

        # A first jump computes only the requested range; later jumps process
        # bounded chunks until the target is reached.
        first = session.advance(
            to_index=4_999,
            view_start=max(0, 4_999 - MAX_VISIBLE_STATES + 1),
            view_end=4_999,
        )
        streamed_states.extend(first["newStates"])
        streamed_alerts.extend(first["newAlerts"])

        backward = session.advance(
            to_index=2_000,
            view_start=1_950,
            view_end=2_000,
        )
        self.assertEqual(backward["newStates"], [])
        self.assertEqual(
            backward["visibleStates"],
            streamed_states[1_950:2_001],
        )

        target = len(records) - 1
        response = session.advance(
            to_index=target,
            view_start=target - MAX_VISIBLE_STATES + 1,
            view_end=target,
        )
        streamed_states.extend(response["newStates"])
        streamed_alerts.extend(response["newAlerts"])
        while response["hasMore"]:
            response = session.advance(
                to_index=target,
                view_start=target - MAX_VISIBLE_STATES + 1,
                view_end=target,
            )
            streamed_states.extend(response["newStates"])
            streamed_alerts.extend(response["newAlerts"])

        candles = backtest.candle_objects(records)
        cct_settings = effective_spec["cct"]
        cst_settings = effective_spec["cst"]
        batch_cst = compute_cst(
            candles,
            swing_neighbors=int(cst_settings["major_swing_neighbors"]),
            break_basis=str(cst_settings["break_basis"]),
        )
        batch_shadow_states = compute_shadow_states(candles)
        batch_states = [
            {
                "index": index,
                "cct": str(
                    classify_cct(
                        candles[
                            max(0, index + 1 - int(cct_settings["window"])) :
                            index + 1
                        ],
                        window=int(cct_settings["window"]),
                        swing_neighbors=int(cct_settings["swing_neighbors"]),
                    )
                ),
                "cst": str(batch_cst[index]),
                "dcState": batch_shadow_states[index]["dc_state"],
                "hierState": batch_shadow_states[index]["hier_state"],
                "mtfCst": None,
                "mtfCct": None,
                "mtfWickPressure": None,
                "mtfClosedAt": None,
            }
            for index in range(len(candles))
        ]

        candle_indexes = {
            str(candle["time"]): index for index, candle in enumerate(records)
        }
        batch_alerts = [
            {
                "candleIndex": candle_indexes[alert["time"]],
                "alert": alert,
            }
            for alert in batch["alerts"]
        ]
        batch_simulation_alerts: dict[int, list[dict]] = {}
        for alert in batch["alerts"]:
            candle_index = candle_indexes[alert["time"]]
            batch_simulation_alerts.setdefault(candle_index, []).append(alert)
        batch_simulation = simulate_batch(
            records,
            batch_simulation_alerts,
            settings=simulation_settings,
        )
        batch_wick_pressure = StreamingWickPressure()
        wick_pressure_by_time = {
            candle.time: str(batch_wick_pressure.update(candle))
            for candle in candles
        }
        batch_snapshots = append_batch_snapshots(
            batch["alerts"],
            path=batch_snapshot_path,
            wick_pressure_by_time=wick_pressure_by_time,
            atr14_by_alert_id=batch_simulation["atr14_by_alert_id"],
            trades=batch_simulation["trades"],
        )
        self.assertEqual(len(records), 10 * 24 * 60)
        self.assertGreater(len(batch_alerts), 0)
        self.assertEqual(canonical_bytes(batch_alerts), canonical_bytes(streamed_alerts))
        self.assertEqual(canonical_bytes(batch_states), canonical_bytes(streamed_states))
        self.assertEqual(
            canonical_bytes(batch_simulation["trades"]),
            canonical_bytes(session.simulation.trades_at(target)),
        )
        self.assertEqual(
            canonical_bytes(batch_simulation["simulation"]),
            canonical_bytes(session.simulation.snapshot(target)),
        )
        session_snapshots = read_snapshots(session_snapshot_path)
        self.assertEqual(canonical_bytes(batch_snapshots), canonical_bytes(session_snapshots))
        self.assertEqual(len(batch_snapshots), len(batch["alerts"]))

        # A second completed replay appends valid records rather than corrupting
        # or replacing the first run's JSONL entries.
        append_batch_snapshots(
            batch["alerts"],
            path=batch_snapshot_path,
            wick_pressure_by_time=wick_pressure_by_time,
            atr14_by_alert_id=batch_simulation["atr14_by_alert_id"],
            trades=batch_simulation["trades"],
        )
        repeated_rows = read_snapshots(batch_snapshot_path)
        self.assertEqual(len(repeated_rows), 2 * len(batch_snapshots))
        self.assertEqual(
            canonical_bytes(repeated_rows[: len(batch_snapshots)]),
            canonical_bytes(repeated_rows[len(batch_snapshots) :]),
        )


class ConfirmedSwingEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = swing_wave_fixture()
        self.spec = backtest.load_spec(SPEC_PATH)

    def test_42_prefix_invariance_for_every_swing_family(self) -> None:
        full_events = compute_confirmed_swing_events(self.records, self.spec)
        prefix_events = compute_confirmed_swing_events(self.records[:42], self.spec)
        visible_full_events = [
            event for event in full_events if event["confirmed_index"] < 42
        ]
        self.assertEqual(prefix_events, visible_full_events)

        expected_families = {"cct_minor", "cst_major", "dc", "hier"}
        self.assertEqual(
            {event["source"] for event in prefix_events},
            expected_families,
        )
        for source, delay in (("cct_minor", 2), ("cst_major", 5)):
            with self.subTest(source=source):
                for event in prefix_events:
                    if event["source"] == source:
                        self.assertEqual(
                            event["confirmed_index"] - event["pivot_index"],
                            delay,
                        )
        self.assertTrue(
            all("level" in event for event in prefix_events if event["source"] == "hier")
        )
        self.assertTrue(
            all("level" not in event for event in prefix_events if event["source"] != "hier")
        )

    def test_session_events_are_causal_and_match_batch_output(self) -> None:
        expected = compute_confirmed_swing_events(self.records, self.spec)
        session = ReplaySession(
            session_id="swing-causality",
            run_id="swing-causality-run",
            timeframe="M15",
            from_value="2025-01-01",
            to_value="2025-01-01",
            records=self.records,
            source="test",
            source_sha256=SOURCE_SHA256,
            spec=self.spec,
            settings_fingerprint="swing-causality-settings",
        )
        self.assertEqual(session.initial_response()["swingEvents"], [])
        streamed: list[dict] = []
        for index in range(1, len(self.records)):
            response = session.advance(
                to_index=index,
                view_start=0,
                view_end=index,
            )
            newly_confirmed = response["newSwingEvents"]
            self.assertTrue(
                all(event["confirmed_index"] == index for event in newly_confirmed)
            )
            self.assertTrue(
                all(
                    event["confirmed_time"] == self.records[index]["time"]
                    for event in newly_confirmed
                )
            )
            streamed.extend(newly_confirmed)
        self.assertEqual(streamed, expected)


if __name__ == "__main__":
    unittest.main()
