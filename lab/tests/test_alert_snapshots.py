import json
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path

from lab.alert_snapshots import (
    append_snapshot,
    build_alert_snapshot,
    read_snapshots,
)
from lab.query import main as query_main, query_snapshots


class AlertSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.alert = {
            "alert_id": "fixture-alert",
            "time": "2025-01-01T01:02:00Z",
            "symbol": "XAUUSD",
            "tf": "M1",
            "direction": "BUY",
            "alert_type": "BUY-A",
            "strength_label": "STRONG",
            "strength_score": "0.625000",
            "strength_features": {"cst_alignment": 1, "engulf": 1},
            "cct": "uptrend",
            "cst": "uptrend",
            "cst_v1_state": "uptrend",
            "cst_v2_state": "consolidation",
            "swing_version_agreement_pct": 0,
            "swing_direction_version": "V2",
            "candle": {
                "open": Decimal("100"),
                "high": Decimal("104"),
                "low": Decimal("98"),
                "close": Decimal("102"),
            },
            "previous_candle": {
                "open": Decimal("101"),
                "high": Decimal("103"),
                "low": Decimal("99"),
                "close": Decimal("100"),
            },
        }

    def test_snapshot_derives_candle_features_and_simulation_fields(self):
        snapshot = build_alert_snapshot(
            self.alert,
            atr14=Decimal("2"),
            wp_state="up-pressure",
            trade={
                "tradeId": "fixture-trade",
                "outcome": "WIN",
                "realizedR": 2.0,
                "netR": 1.9,
                "netPnl": 190.0,
            },
        )

        self.assertEqual(snapshot["symbol"], "XAUUSD")
        self.assertEqual(snapshot["tf"], "M1")
        self.assertEqual(snapshot["variant"], "A")
        self.assertEqual(snapshot["strength_score"], "0.625000")
        self.assertEqual(snapshot["cst_v1_state"], "uptrend")
        self.assertEqual(snapshot["cst_v2_state"], "consolidation")
        self.assertEqual(snapshot["swing_version_agreement_pct"], 0)
        self.assertEqual(snapshot["swing_direction_version"], "V2")
        self.assertEqual(
            snapshot["strength_features"],
            {"cst_alignment": 1, "engulf": 1},
        )
        self.assertTrue(snapshot["engulf"])
        self.assertEqual(snapshot["alert_candle_ohlc"]["open"], "100")
        self.assertEqual(snapshot["prev_candle_ohlc"]["close"], "100")
        self.assertEqual(Decimal(snapshot["body_ratio"]), Decimal(2) / Decimal(6))
        self.assertEqual(
            Decimal(snapshot["lower_wick_frac"]), Decimal(2) / Decimal(6)
        )
        self.assertEqual(
            Decimal(snapshot["upper_wick_frac"]), Decimal(2) / Decimal(6)
        )
        self.assertEqual(Decimal(snapshot["penetration_atr"]), Decimal("0.5"))
        self.assertEqual(snapshot["hour_utc"], 1)
        self.assertEqual(snapshot["day_of_week"], 2)
        self.assertEqual(snapshot["sim_trade_id"], "fixture-trade")
        self.assertEqual(snapshot["sim_outcome"], "WIN")
        self.assertEqual(snapshot["r_multiple_achieved"], "2.0")
        self.assertEqual(snapshot["sim_r_after_costs"], "1.9")
        self.assertEqual(snapshot["sim_net_pnl"], "190.0")

    def test_jsonl_appends_complete_valid_records_across_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshots.jsonl"
            snapshot = build_alert_snapshot(
                self.alert,
                atr14=Decimal("2"),
                wp_state="up-pressure",
            )

            self.assertEqual(append_snapshot(path, snapshot), 0)
            self.assertEqual(append_snapshot(path, snapshot), 1)
            rows = read_snapshots(path)

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows, [snapshot, snapshot])
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)
            for line in path.read_text(encoding="utf-8").splitlines():
                self.assertEqual(json.loads(line), snapshot)

    def test_query_filters_and_summarizes_matching_resolved_trades(self):
        rows = [
            {
                "variant": "B",
                "strength": "STRONG",
                "wp_state": "up-pressure",
                "engulf": True,
                "direction": "BUY",
                "sim_outcome": "WIN",
                "r_multiple_achieved": "2.0",
                "sim_r_after_costs": "1.8",
                "sim_net_pnl": "18.0",
            },
            {
                "variant": "B",
                "strength": "STRONG",
                "wp_state": "down-pressure",
                "engulf": True,
                "direction": "BUY",
                "sim_outcome": "LOSS",
                "r_multiple_achieved": "-1.0",
                "sim_r_after_costs": "-1.2",
                "sim_net_pnl": "-12.0",
            },
            {
                "variant": "B",
                "strength": "WEAK",
                "wp_state": "up-pressure",
                "engulf": False,
                "direction": "SELL",
                "sim_outcome": "WIN",
                "r_multiple_achieved": "2.0",
                "sim_r_after_costs": "1.8",
                "sim_net_pnl": "18.0",
            },
        ]
        result = query_snapshots(
            rows,
            variant="B",
            strength="STRONG",
            wp="up",
            engulf=True,
            direction="BUY",
        )

        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["summary"]["win_rate_pct"], Decimal("100"))
        self.assertEqual(result["summary"]["average_r"], Decimal("2.0"))
        self.assertEqual(
            result["summary"]["expectancy_after_costs"], Decimal("1.8")
        )
        self.assertEqual(
            result["summary"]["total_pnl_after_costs"], Decimal("18.0")
        )

    def test_query_cli_prints_the_filtered_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshots.jsonl"
            row = {
                "variant": "A",
                "strength": "STRONG",
                "wp_state": "balanced",
                "engulf": True,
                "direction": "SELL",
                "sim_outcome": "WIN",
                "r_multiple_achieved": "1.5",
                "sim_r_after_costs": "1.3",
                "sim_net_pnl": "13.0",
            }
            append_snapshot(path, row)
            output = StringIO()
            with redirect_stdout(output):
                status = query_main(
                    ["--snapshots", str(path), "--variant", "A", "--engulf"]
                )

        self.assertEqual(status, 0)
        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "Matching alerts: 1",
                "Resolved simulated trades: 1",
                "Win rate: 100.0% (1/1)",
                "Average R: +1.50",
                "Expectancy after costs: +1.30R",
                "Total P&L after costs: +$13.00",
            ],
        )

    def test_direction_aliases_and_geometry_weights_are_persisted(self) -> None:
        from lab.alert_snapshots import build_alert_snapshot

        alert = {
            "alert_id": "gold-direction-snapshot",
            "symbol": "XAUUSD",
            "time": "2025-01-03T10:15:00Z",
            "direction": "BUY",
            "alert_type": "BUY-A",
            "swing_direction_version": "V2",
            "candle": {
                "time": "2025-01-03T10:15:00Z",
                "open": 100,
                "high": 103,
                "low": 98,
                "close": 102,
            },
            "previous_candle": {
                "time": "2025-01-03T10:10:00Z",
                "open": 100,
                "high": 102,
                "low": 97,
                "close": 99,
            },
        }
        trade = {
            "tradeId": "trade-inverted",
            "directionMode": "inverted",
            "sourceDirection": "BUY",
            "appliedDirection": "SELL",
            "invertScore": 0.75,
            "inverted": True,
            "exitModel": "candle_geometry",
            "geometryWeights": {
                "bodyWeight": 1,
                "wickMultiplier": 3,
                "rangeMultiplier": 4,
            },
            "outcome": "WIN",
            "realizedR": 1.5,
            "netR": 1.3,
            "netPnl": 13,
        }
        fade_trade = {"outcome": "LOSS", "netR": -1}

        snapshot = build_alert_snapshot(
            alert,
            atr14=2,
            wp_state="balanced",
            trade=trade,
            fade_trade=fade_trade,
        )

        self.assertEqual(snapshot["direction_mode"], "inverted")
        self.assertEqual(snapshot["invert_score"], "0.75")
        self.assertEqual(snapshot["applied_direction"], "SELL")
        self.assertTrue(snapshot["is_inverted"])
        self.assertEqual(snapshot["swing_version"], "V2")
        self.assertEqual(
            snapshot["geometry_weights"],
            {"body_weight": "1", "wick_multiplier": "3", "range_multiplier": "4"},
        )
        self.assertEqual(snapshot["sim_fade_r_after_costs"], "-1")

        settings_snapshot = build_alert_snapshot(
            alert,
            atr14=2,
            wp_state="balanced",
            simulation_settings={
                "directionMode": "inverted",
                "exitModel": "candle_geometry",
                "stopWickMultiplier": 3,
                "swingVersion": "V1",
            },
        )
        self.assertEqual(settings_snapshot["direction_mode"], "inverted")
        self.assertEqual(settings_snapshot["applied_direction"], "SELL")
        self.assertIsNotNone(settings_snapshot["invert_score"])
        self.assertEqual(settings_snapshot["swing_version"], "V1")
        self.assertEqual(
            settings_snapshot["geometry_weights"]["wick_multiplier"], "3"
        )

    def test_replay_persists_mirror_outcome_after_both_sides_resolve(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from lab.replay import ReplaySession

        class SimulationStub:
            mirror_trades = [
                {"alertId": "alert-fade", "outcome": "OPEN", "netR": None}
            ]

            def mirror_trades_at(self, _index: int):
                return self.mirror_trades

        with tempfile.TemporaryDirectory() as temporary:
            session = object.__new__(ReplaySession)
            session.snapshot_path = Path(temporary) / "snapshots.jsonl"
            session._pending_snapshots = {
                "alert-fade": {
                    "alert_id": "alert-fade",
                    "sim_outcome": "WIN",
                    "sim_fade_outcome": None,
                }
            }
            session.simulation = SimulationStub()
            session._refresh_pending_fade_snapshots(3)
            self.assertFalse(session.snapshot_path.exists())
            self.assertIn("alert-fade", session._pending_snapshots)

            session.simulation.mirror_trades = [
                {"alertId": "alert-fade", "outcome": "LOSS", "netR": -1.25}
            ]
            session._refresh_pending_fade_snapshots(4)

            rows = [
                json.loads(line)
                for line in session.snapshot_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sim_fade_outcome"], "LOSS")
            self.assertEqual(rows[0]["sim_fade_r_after_costs"], "-1.25")
            self.assertNotIn("alert-fade", session._pending_snapshots)


if __name__ == "__main__":
    unittest.main()
