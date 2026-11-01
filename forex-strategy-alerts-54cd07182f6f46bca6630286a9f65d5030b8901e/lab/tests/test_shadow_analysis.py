from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import unittest

from lab import backtest
from lab.digest import build_digest
from lab.engine import Candle
from lab.query import query_snapshots
from lab.replay import REPLAY_ADVANCE_CHUNK, ReplaySession
from lab.shadow_detectors import (
    StreamingDirectionalChange,
    StreamingHierarchicalSwings,
    StreamingSimpleATR,
    compute_shadow_states,
)
from lab.simulation import SimulationSession

SPEC_PATH = Path(__file__).parents[1] / "specs" / "seed_v1.5.0.json"
SOURCE_SHA256 = "fixture-source-hash"


def make_candles(count: int = 80) -> list[Candle]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles = []
    previous_close = Decimal("2000")
    for index in range(count):
        change = Decimal((index * 7) % 11 - 5) / Decimal("10")
        close = previous_close + change
        opened = previous_close
        high = max(opened, close) + Decimal("0.25") + Decimal(index % 3) / 100
        low = min(opened, close) - Decimal("0.25") - Decimal(index % 2) / 100
        timestamp = (start + timedelta(minutes=index * 5)).isoformat().replace(
            "+00:00", "Z"
        )
        candles.append(
            Candle.from_values(timestamp, opened, high, low, close)
        )
        previous_close = close
    return candles


def records_from(candles: list[Candle]) -> list[dict]:
    return [
        {
            "time": candle.time,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
        }
        for candle in candles
    ]


class ShadowDetectorTests(unittest.TestCase):
    def test_atr_matches_replay_simulator_and_dc_confirms_closed_candle_reversals(self):
        flat = [
            Candle.from_values(
                f"2025-01-01T00:{index:02d}:00Z",
                "100",
                "101",
                "99",
                "100",
            )
            for index in range(14)
        ]
        flat.extend(
            [
                Candle.from_values("2025-01-01T00:14:00Z", "100", "104", "100", "103"),
                Candle.from_values("2025-01-01T00:15:00Z", "103", "104", "98", "98"),
            ]
        )
        records = records_from(flat)
        simulation = SimulationSession(
            records,
            symbol="XAUUSD",
            settings={"enabled": False},
        )
        atr = StreamingSimpleATR()
        dc = StreamingDirectionalChange()
        states = []
        for index, candle in enumerate(flat):
            simulation.process_candle(index)
            streamed_atr = atr.update(candle)
            self.assertEqual(streamed_atr, simulation.atr_at(index))
            states.append(dc.update(candle, streamed_atr))

        self.assertEqual(states[13], "undefined")
        self.assertEqual(states[14], "uptrend")
        self.assertEqual(states[15], "downtrend")

    def test_all_42_prefixes_are_identical_to_their_batch_prefix(self):
        candles = make_candles(64)
        complete = compute_shadow_states(candles)
        for prefix_length in range(1, 43):
            with self.subTest(prefix_length=prefix_length):
                prefix = compute_shadow_states(candles[:prefix_length])
                self.assertEqual(prefix, complete[:prefix_length])
                for index, state in enumerate(prefix):
                    for event in state["hier_promotions"]:
                        self.assertEqual(event["discovered_at"], index)
                        self.assertLessEqual(event["pivot_index"], index)

    def test_replay_stream_matches_batch_shadow_state(self):
        candles = make_candles(72)
        records = records_from(candles)
        spec = backtest.load_spec(SPEC_PATH)
        fingerprint = backtest.settings_fingerprint(spec)
        session = ReplaySession(
            session_id="shadow-parity",
            run_id="shadow-fixture",
            timeframe="M5",
            from_value="2025-01-01",
            to_value="2025-01-02",
            records=records,
            source="synthetic-fixture",
            source_sha256=SOURCE_SHA256,
            spec=spec,
            settings_fingerprint=fingerprint,
            simulation_settings={"enabled": False},
        )
        session.initial_response()
        target = len(records) - 1
        while session.computed_through < target:
            next_index = min(
                target,
                session.computed_through + REPLAY_ADVANCE_CHUNK,
            )
            session.advance(
                to_index=next_index,
                view_start=max(0, next_index - 999),
                view_end=next_index,
            )

        batch = compute_shadow_states(candles)
        self.assertEqual(
            [state["dcState"] for state in session.states],
            [state["dc_state"] for state in batch],
        )
        self.assertEqual(
            [state["hierState"] for state in session.states],
            [state["hier_state"] for state in batch],
        )
        self.assertEqual(
            [state["hierPromotions"] for state in session.states],
            [state["hier_promotions"] for state in batch],
        )

    def test_hierarchical_fixture_promotes_through_depth_three_causally(self):
        pivot_highs = [1, 5, 2, 10, 3, 6, 2, 14, 3, 7, 2, 12, 3, 8, 1]
        high_values = [Decimal(value + 100) for value in pivot_highs]
        fixture: list[Candle] = []

        def append_candle(high: Decimal, low: Decimal) -> None:
            value = (high + low) / Decimal("2")
            timestamp = datetime(
                2025, 1, 1, tzinfo=timezone.utc
            ) + timedelta(minutes=5 * len(fixture))
            fixture.append(
                Candle.from_values(
                    timestamp.isoformat().replace("+00:00", "Z"),
                    value,
                    high,
                    low,
                    value,
                )
            )

        append_candle(high_values[0] - 2, high_values[0] - 4)
        append_candle(high_values[0] - 1, high_values[0] - 3)
        for index, value in enumerate(high_values):
            append_candle(value, value - 2)
            if index + 1 < len(high_values):
                valley = min(value, high_values[index + 1]) - 2
                append_candle(valley, valley - 2)
                append_candle(valley + Decimal("0.25"), valley - 1)
        append_candle(high_values[-1] - 1, high_values[-1] - 3)
        append_candle(high_values[-1] - 2, high_values[-1] - 4)

        detector = StreamingHierarchicalSwings()
        depths = []
        for index, candle in enumerate(fixture):
            _state, promotions = detector.update(candle)
            for event in promotions:
                depths.append(event["depth"])
                self.assertEqual(event["discovered_at"], index)
                self.assertLess(event["pivot_index"], event["discovered_at"])
        self.assertIn(1, depths)
        self.assertIn(2, depths)
        self.assertIn(3, depths)


class SnapshotAnalysisTests(unittest.TestCase):
    def test_cst_shadow_crosstab_reports_directional_agreement_and_outcomes(self):
        snapshots = []
        for index in range(30):
            shadow_state = "uptrend" if index < 20 else "downtrend"
            outcome = "WIN" if index % 2 == 0 else "LOSS"
            snapshots.append(
                {
                    "cst": "uptrend",
                    "dc_state": shadow_state,
                    "sim_outcome": outcome,
                    "r_multiple_achieved": "2" if outcome == "WIN" else "-1",
                    "sim_r_after_costs": "1.8" if outcome == "WIN" else "-1.2",
                    "sim_net_pnl": "100" if outcome == "WIN" else "-50",
                }
            )

        result = query_snapshots(snapshots, crosstab="cst,dc")
        table = result["crosstab"]
        self.assertEqual(table["directional_comparison_count"], 30)
        self.assertEqual(table["agreement_count"], 20)
        self.assertEqual(table["agreement_pct"], Decimal("66.66666666666666666666666667"))
        self.assertEqual(
            sum(cell["resolved_trade_count"] for cell in table["cells"]),
            30,
        )

    def test_digest_applies_sample_floor_and_stays_compact_without_scored_results(self):
        snapshots = []
        for index in range(59):
            snapshots.append(
                {
                    "variant": "A" if index < 30 else "B",
                    "direction": "BUY",
                    "strength": "STRONG",
                    "wp_state": "balanced",
                    "engulf": False,
                    "cst": "uptrend",
                    "dc_state": "uptrend",
                    "hier_state": "undefined",
                    "period": "2025-01",
                    "sim_outcome": None,
                }
            )

        digest = build_digest(snapshots, min_cell_size=30)
        variant_cells = [
            cell for cell in digest["groups"] if cell["axis"] == "variant"
        ]
        self.assertEqual([cell["value"] for cell in variant_cells], ["A"])
        self.assertEqual(variant_cells[0]["n"], 30)
        self.assertIsNone(variant_cells[0]["expectancy_after_costs"])
        self.assertLessEqual(digest["estimated_tokens"], 2_000)
        self.assertNotIn("validation", digest)
        self.assertNotIn("alert_candle_ohlc", digest)


if __name__ == "__main__":
    unittest.main()
