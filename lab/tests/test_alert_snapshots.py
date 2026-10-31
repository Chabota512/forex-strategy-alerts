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
            "cct": "uptrend",
            "cst": "uptrend",
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


if __name__ == "__main__":
    unittest.main()
