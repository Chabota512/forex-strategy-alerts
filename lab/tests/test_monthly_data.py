from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from lab.candle_data import sha256_file
from lab.monthly_data import (
    EXPECTED_MERGED_ROWS,
    MONTHS,
    MonthlyDataError,
    _fingerprint_rows,
    load_monthly_manifest,
    load_monthly_timeframe,
    monthly_file_path,
    verify_monthly_file_hashes,
)
from lab.evolve import load_approved_market_data


class MonthlyDataStitchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write_month(self, month: str, times: list[str]) -> dict[str, object]:
        path = monthly_file_path(self.root, "TEST", "M1", month)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=["time", "open", "high", "low", "close", "source"],
            )
            writer.writeheader()
            for index, timestamp in enumerate(times):
                price = str(100 + index)
                writer.writerow(
                    {
                        "time": timestamp,
                        "open": price,
                        "high": str(101 + index),
                        "low": str(99 + index),
                        "close": price,
                        "source": "fixture",
                    }
                )
        return {
            "row_count": len(times),
            "sha256": sha256_file(path),
        }

    def test_stitches_months_in_bar_open_time_order(self) -> None:
        records = {
            "2023-01": self.write_month(
                "2023-01",
                ["2023-01-31T23:58:00Z", "2023-01-31T23:59:00Z"],
            ),
            "2023-02": self.write_month(
                "2023-02",
                ["2023-02-01T00:00:00Z", "2023-02-01T00:01:00Z"],
            ),
        }
        data = load_monthly_timeframe(
            self.root,
            "TEST",
            "M1",
            start_month="2023-01",
            end_month="2023-02",
            manifest={"months": list(records), "timeframes": {"M1": records}},
        )

        self.assertEqual(
            [str(candle["time"]) for candle in data.candles],
            [
                "2023-01-31T23:58:00Z",
                "2023-01-31T23:59:00Z",
                "2023-02-01T00:00:00Z",
                "2023-02-01T00:01:00Z",
            ],
        )

    def test_rejects_a_bar_outside_its_month_of_open(self) -> None:
        records = {
            "2023-01": self.write_month("2023-01", ["2023-01-31T23:59:00Z"]),
            "2023-02": self.write_month("2023-02", ["2023-01-31T23:59:00Z"]),
        }

        with self.assertRaisesRegex(MonthlyDataError, "outside 2023-02"):
            load_monthly_timeframe(
                self.root,
                "TEST",
                "M1",
                start_month="2023-01",
                end_month="2023-02",
                manifest={"months": list(records), "timeframes": {"M1": records}},
            )

    def test_numeric_fingerprint_ignores_csv_decimal_formatting(self) -> None:
        first = {
            "time": "2023-01-02T00:00:00Z",
            "open": "1.0",
            "high": "2.00",
            "low": "0.50",
            "close": "1.500",
        }
        serialized_differently = {
            **first,
            "open": "1.0000",
            "high": "2",
            "low": "0.50000",
            "close": "1.5",
        }

        self.assertEqual(
            _fingerprint_rows([first]),
            _fingerprint_rows([serialized_differently]),
        )

    def test_monthly_hash_gate_checks_every_manifest_file(self) -> None:
        records = {}
        paths = {}
        for timeframe in ("M1", "M5"):
            path = monthly_file_path(
                self.root, "XAUUSD", timeframe, "2023-01"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{timeframe} fixture\n", encoding="utf-8")
            paths[timeframe] = path
            records[timeframe] = {
                "2023-01": {"sha256": sha256_file(path)}
            }

        verify_monthly_file_hashes(
            self.root,
            manifest={"timeframes": records},
        )

        with paths["M5"].open("a", encoding="utf-8") as stream:
            stream.write("changed\n")
        with self.assertRaisesRegex(MonthlyDataError, "hash differs"):
            verify_monthly_file_hashes(
                self.root,
                manifest={"timeframes": records},
            )

    def test_xauusd_monthly_m1_matches_every_raw_bar(self) -> None:
        manifest = load_monthly_manifest()

        self.assertEqual(manifest["months"], list(MONTHS))
        self.assertEqual(len(manifest["months"]), 36)
        self.assertEqual(manifest["source_rows"], EXPECTED_MERGED_ROWS)
        self.assertEqual(manifest["status"], "OWNER_APPROVED")
        self.assertIs(manifest["owner_approved"], True)
        market_data = load_approved_market_data()
        self.assertEqual(
            market_data.source_sha256,
            "21de3e092ab6b24c37ead1790e83814d294a092f1dfe9eac192721757aab361c",
        )
        self.assertEqual(market_data.source, "dukascopy")
        self.assertEqual(
            set(market_data.records_by_timeframe),
            {"M5", "M15", "H1", "H4", "D1"},
        )


if __name__ == "__main__":
    unittest.main()
