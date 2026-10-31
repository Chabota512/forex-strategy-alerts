from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from lab.alert_snapshots import read_snapshots
from lab.candle_data import sha256_file
from lab.data_sanity import sanity_manifest_path
from lab.populate_snapshots import populate_snapshots


def _write_year(path: Path) -> None:
    five_minute_bars = (
        {"open": 100, "high": 101, "low": 99, "close": 100.5},
        {"open": 100.5, "high": 102, "low": 99.5, "close": 100},
        {"open": 100, "high": 105, "low": 98.5, "close": 99.8},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("time", "open", "high", "low", "close", "source"),
        )
        writer.writeheader()
        for month in range(1, 13):
            start = datetime(2025, month, 1, tzinfo=timezone.utc)
            for bar_index in range(24 * 60 // 5):
                bar = five_minute_bars[bar_index % len(five_minute_bars)]
                for minute in range(5):
                    opened = Decimal(str(bar["open"]))
                    closed = (
                        Decimal(str(bar["close"]))
                        if minute == 4
                        else opened
                    )
                    high = Decimal(str(bar["high"])) if minute == 0 else max(opened, closed)
                    low = Decimal(str(bar["low"])) if minute == 0 else min(opened, closed)
                    timestamp = (
                        start + timedelta(minutes=bar_index * 5 + minute)
                    ).isoformat().replace("+00:00", "Z")
                    writer.writerow(
                        {
                            "time": timestamp,
                            "open": format(opened, "f"),
                            "high": format(high, "f"),
                            "low": format(low, "f"),
                            "close": format(closed, "f"),
                            "source": "dukascopy",
                        }
                    )


def _write_days(path: Path, day_count: int) -> None:
    five_minute_bars = (
        {"open": 100, "high": 101, "low": 99, "close": 100.5},
        {"open": 100.5, "high": 102, "low": 99.5, "close": 100},
        {"open": 100, "high": 105, "low": 98.5, "close": 99.8},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("time", "open", "high", "low", "close", "source"),
        )
        writer.writeheader()
        for day_offset in range(day_count):
            start = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(
                days=day_offset
            )
            for bar_index in range(24 * 60 // 5):
                bar = five_minute_bars[(bar_index + day_offset) % len(five_minute_bars)]
                for minute in range(5):
                    opened = Decimal(str(bar["open"]))
                    closed = (
                        Decimal(str(bar["close"]))
                        if minute == 4
                        else opened
                    )
                    high = Decimal(str(bar["high"])) if minute == 0 else max(opened, closed)
                    low = Decimal(str(bar["low"])) if minute == 0 else min(opened, closed)
                    timestamp = (
                        start + timedelta(minutes=bar_index * 5 + minute)
                    ).isoformat().replace("+00:00", "Z")
                    writer.writerow(
                        {
                            "time": timestamp,
                            "open": format(opened, "f"),
                            "high": format(high, "f"),
                            "low": format(low, "f"),
                            "close": format(closed, "f"),
                            "source": "dukascopy",
                        }
                    )


class SnapshotPopulationTests(unittest.TestCase):
    def test_full_year_population_links_alerts_to_resolved_trades_and_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            input_path = data_dir / "XAUUSD_M1.csv"
            output_path = Path(temporary) / "snapshots.jsonl"
            _write_year(input_path)
            manifest_path = sanity_manifest_path(data_dir, "XAUUSD", "M1")
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "status": "OWNER_APPROVED",
                        "owner_approved": False,
                        "review_method": "OWNER_SPOTCHECK",
                        "data_complete": True,
                        "symbol": "XAUUSD",
                        "timeframe": "M1",
                        "primary_sha256": sha256_file(input_path),
                    }
                ),
                encoding="utf-8",
            )

            result = populate_snapshots(
                input_path=input_path,
                output_path=output_path,
            )
            rows = read_snapshots(output_path)
            resolved = [
                row
                for row in rows
                if row.get("sim_outcome") in {"WIN", "LOSS"}
            ]

            self.assertTrue(result["scored"])
            self.assertGreater(result["resolved_trade_snapshots"], 0)
            self.assertGreater(result["expectancy_cells"], 0)
            self.assertTrue(
                any(
                    group.get("expectancy_after_costs") is not None
                    for group in result["digest"]["groups"]
                )
            )
            self.assertEqual(len(resolved), result["resolved_trade_snapshots"])
            self.assertTrue(
                all(row.get("sim_trade_id") and row.get("sim_r_after_costs") is not None
                    for row in resolved)
            )

    def test_pending_data_override_is_test_only_and_scoped_to_date_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            input_path = data_dir / "XAUUSD_M1.csv"
            output_path = Path(temporary) / "pending-test-snapshots.jsonl"
            _write_days(input_path, 30)
            manifest_path = sanity_manifest_path(data_dir, "XAUUSD", "M1")
            manifest_path.parent.mkdir(parents=True)
            manifest = {
                "status": "PENDING_OWNER_SPOTCHECK",
                "owner_approved": False,
                "review_method": "OWNER_SPOTCHECK",
                "data_complete": True,
                "symbol": "XAUUSD",
                "timeframe": "M1",
                "primary_sha256": sha256_file(input_path),
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "not owner-approved"):
                populate_snapshots(
                    input_path=input_path,
                    output_path=output_path,
                    from_date="2025-01-01",
                    to_date="2025-01-30",
                )

            result = populate_snapshots(
                input_path=input_path,
                output_path=output_path,
                from_date="2025-01-01",
                to_date="2025-01-30",
                allow_pending_for_test=True,
            )
            rows = read_snapshots(output_path)
            saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(result["window"], {"from": "2025-01-01", "to": "2025-01-30"})
            self.assertTrue(result["approval_override_for_test"])
            self.assertGreater(result["resolved_trade_snapshots"], 0)
            self.assertGreater(result["expectancy_cells"], 0)
            self.assertEqual(saved_manifest["status"], "PENDING_OWNER_SPOTCHECK")
            self.assertFalse(saved_manifest["owner_approved"])
            self.assertTrue(rows)


if __name__ == "__main__":
    unittest.main()
