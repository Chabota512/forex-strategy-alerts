from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from lab.candle_data import sha256_file
from lab.data_onboarding import onboard_symbol
from lab.data_sanity import require_owner_approved_sanity


def _write_source(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp_utc", "open", "high", "low", "close"])
        for timestamp, close in rows:
            writer.writerow([timestamp, "100", "102", "99", close])


def _monthly_rows(year: int) -> list[tuple[str, str]]:
    return [
        (f"{year}-{month:02d}-01T00:00:00Z", "101")
        for month in range(1, 13)
    ]


class DataOnboardingTests(unittest.TestCase):
    def test_complete_yearly_inputs_merge_dedupe_and_stay_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            input_dir = data_dir / "downloads"
            for year in (2023, 2024, 2025):
                rows = _monthly_rows(year)
                if year == 2024:
                    rows.append(("2024-12-31T23:59:00Z", "101"))
                if year == 2025:
                    rows.insert(0, ("2024-12-31T23:59:00Z", "101"))
                _write_source(
                    input_dir / f"GBPJPY_M1_BID_{year}_UTC.csv",
                    rows,
                )

            manifest = onboard_symbol("GBPJPY", data_dir=data_dir)
            canonical = data_dir / "GBPJPY_M1.csv"
            manifest_path = data_dir / "sanity_checks" / "GBPJPY_M1.json"

            self.assertEqual(manifest["status"], "PENDING_OWNER_SPOTCHECK")
            self.assertFalse(manifest["owner_approved"])
            self.assertTrue(manifest["data_complete"])
            self.assertEqual(manifest["missing_years"], [])
            self.assertEqual(manifest["missing_months"], [])
            self.assertEqual(manifest["overlap_rows_deduplicated"], 1)
            self.assertEqual(manifest["year_rows"], {
                "2023": 12,
                "2024": 13,
                "2025": 12,
            })
            self.assertEqual(manifest["local_integrity"]["status"], "PASS")
            self.assertTrue(canonical.is_file())
            self.assertIn(",dukascopy", canonical.read_text(encoding="utf-8"))
            self.assertTrue(
                (
                    data_dir
                    / "historicalMarketData"
                    / "GBPJPY"
                    / "GBPJPY_M1_BID_2023_UTC.csv"
                ).is_file()
            )
            with self.assertRaisesRegex(ValueError, "not owner-approved"):
                require_owner_approved_sanity(
                    data_dir,
                    symbol="GBPJPY",
                    timeframe="M1",
                    primary_sha256=sha256_file(canonical),
                )

            approved = json.loads(manifest_path.read_text(encoding="utf-8"))
            approved["status"] = "OWNER_APPROVED"
            manifest_path.write_text(json.dumps(approved), encoding="utf-8")
            require_owner_approved_sanity(
                data_dir,
                symbol="GBPJPY",
                timeframe="M1",
                primary_sha256=sha256_file(canonical),
            )

    def test_incomplete_inputs_do_not_replace_existing_canonical_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            canonical = data_dir / "XAUUSD_M1.csv"
            canonical.parent.mkdir(parents=True)
            original = (
                "time,open,high,low,close,source\n"
                "2025-01-01T00:00:00Z,100,102,99,101,dukascopy\n"
            )
            canonical.write_text(original, encoding="utf-8")
            _write_source(
                data_dir / "downloads" / "XAUUSD_M1_BID_2025_UTC.csv",
                _monthly_rows(2025),
            )

            manifest = onboard_symbol("XAUUSD", data_dir=data_dir)

            self.assertFalse(manifest["data_complete"])
            self.assertEqual(manifest["missing_years"], [2023, 2024])
            self.assertEqual(canonical.read_text(encoding="utf-8"), original)
            self.assertEqual(manifest["status"], "PENDING_OWNER_SPOTCHECK")

    def test_conflicting_boundary_rows_block_canonical_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            for year in (2023, 2024, 2025):
                rows = _monthly_rows(year)
                if year == 2024:
                    rows.append(("2024-12-31T23:59:00Z", "101"))
                if year == 2025:
                    rows.insert(0, ("2024-12-31T23:59:00Z", "100.5"))
                _write_source(
                    data_dir
                    / "downloads"
                    / f"AUDJPY_M1_BID_{year}_UTC.csv",
                    rows,
                )

            manifest = onboard_symbol("AUDJPY", data_dir=data_dir)

            self.assertFalse(manifest["data_complete"])
            self.assertIsNone(manifest["primary_file"])
            self.assertTrue(
                any("Conflicting OHLC values" in error for error in manifest["errors"])
            )
            self.assertFalse((data_dir / "AUDJPY_M1.csv").exists())

    def test_audjpy_2023_is_archived_but_not_in_required_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            input_dir = data_dir / "downloads"
            for year in (2023, 2024, 2025):
                _write_source(
                    input_dir / f"AUDJPY_M1_BID_{year}_UTC.csv",
                    _monthly_rows(year),
                )

            manifest = onboard_symbol("AUDJPY", data_dir=data_dir)
            canonical = data_dir / "AUDJPY_M1.csv"
            archived_extra = (
                data_dir
                / "historicalMarketData"
                / "AUDJPY"
                / "AUDJPY_M1_BID_2023_UTC.csv"
            )
            rows = canonical.read_text(encoding="utf-8").splitlines()

            self.assertTrue(manifest["data_complete"])
            self.assertEqual(manifest["required_years"], [2024, 2025])
            self.assertEqual(manifest["available_source_years"], [2023, 2024, 2025])
            self.assertEqual(manifest["unrequested_source_years"], [2023])
            self.assertEqual(manifest["year_rows"], {"2024": 12, "2025": 12})
            self.assertEqual(len(manifest["source_files"]), 2)
            self.assertEqual(len(manifest["unrequested_source_files"]), 1)
            self.assertTrue(archived_extra.is_file())
            self.assertTrue(rows[1].startswith("2024-01-01T00:00:00Z,"))
            self.assertTrue(rows[-1].startswith("2025-12-01T00:00:00Z,"))


if __name__ == "__main__":
    unittest.main()
