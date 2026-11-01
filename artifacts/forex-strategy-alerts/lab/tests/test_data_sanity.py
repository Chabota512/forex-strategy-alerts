import csv
import json
import tempfile
import unittest
from pathlib import Path

from lab.candle_data import load_candle_file
from lab.data_sanity import (
    compare_candle_sources,
    require_owner_approved_sanity,
    sanity_manifest_path,
)


class DataSanityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.primary = self.data_dir / "GBPUSD_M1.csv"
        self.reference = self.root / "yahoo.csv"
        self.report_path = sanity_manifest_path(self.data_dir, "GBPUSD", "M1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, path, rows, fields):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_comparison_reports_exact_timestamp_differences_without_auto_approval(self):
        self.write(
            self.primary,
            [
                {"time": "2026-01-01T00:00:00Z", "open": "1.2", "high": "1.3", "low": "1.1", "close": "1.25", "source": "dukascopy"},
                {"time": "2026-01-01T00:01:00Z", "open": "1.25", "high": "1.4", "low": "1.2", "close": "1.35", "source": "dukascopy"},
            ],
            ("time", "open", "high", "low", "close", "source"),
        )
        self.write(
            self.reference,
            [
                {"Datetime": "2026-01-01T00:00:00Z", "Close": "1.2501"},
                {"Datetime": "2026-01-01T00:01:00Z", "Close": "1.35"},
                {"Datetime": "2026-01-01T00:02:00Z", "Close": "1.36"},
            ],
            ("Datetime", "Close"),
        )

        report = compare_candle_sources(
            self.primary,
            self.reference,
            symbol="GBPUSD",
            timeframe="M1",
            report_path=self.report_path,
            max_samples=10,
        )
        self.assertEqual(report["status"], "PENDING_OWNER_REVIEW")
        self.assertFalse(report["owner_approved"])
        self.assertEqual(report["comparison"]["matched_timestamps"], 2)
        self.assertEqual(report["comparison"]["sampled_nonzero_close_differences"], 1)
        self.assertEqual(report["comparison"]["primary_only_timestamps"], 0)
        self.assertEqual(report["comparison"]["reference_only_timestamps"], 1)
        self.assertTrue(self.report_path.is_file())

    def test_real_data_gate_requires_owner_approval_and_matching_hashes(self):
        self.write(
            self.primary,
            [
                {"time": "2026-01-01T00:00:00Z", "open": "1.2", "high": "1.3", "low": "1.1", "close": "1.25", "source": "dukascopy"},
            ],
            ("time", "open", "high", "low", "close", "source"),
        )
        self.write(
            self.reference,
            [{"Datetime": "2026-01-01T00:00:00Z", "Close": "1.2501"}],
            ("Datetime", "Close"),
        )
        report = compare_candle_sources(
            self.primary,
            self.reference,
            symbol="GBPUSD",
            timeframe="M1",
            report_path=self.report_path,
        )
        primary = load_candle_file(self.primary)
        with self.assertRaisesRegex(ValueError, "not owner-approved"):
            require_owner_approved_sanity(
                self.data_dir,
                symbol="GBPUSD",
                timeframe="M1",
                primary_sha256=primary.sha256,
            )

        report["status"] = "OWNER_APPROVED"
        report["owner_approved"] = True
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        require_owner_approved_sanity(
            self.data_dir,
            symbol="GBPUSD",
            timeframe="M1",
            primary_sha256=primary.sha256,
        )

        with self.primary.open("a", encoding="utf-8") as stream:
            stream.write(
                "2026-01-01T00:01:00Z,1.25,1.4,1.2,1.35,dukascopy\n"
            )
        changed = load_candle_file(self.primary)
        with self.assertRaisesRegex(ValueError, "changed since its sanity check"):
            require_owner_approved_sanity(
                self.data_dir,
                symbol="GBPUSD",
                timeframe="M1",
                primary_sha256=changed.sha256,
            )

    def test_owner_accepted_training_data_gate_requires_basis_and_matching_hash(self):
        self.write(
            self.primary,
            [
                {
                    "time": "2026-01-01T00:00:00Z",
                    "open": "1.2",
                    "high": "1.3",
                    "low": "1.1",
                    "close": "1.25",
                    "source": "dukascopy",
                },
            ],
            ("time", "open", "high", "low", "close", "source"),
        )
        primary = load_candle_file(self.primary)
        report = {
            "symbol": "GBPUSD",
            "timeframe": "M1",
            "primary_sha256": primary.sha256,
            "status": "OWNER_APPROVED",
            "owner_approved": True,
            "review_method": "OWNER_ACCEPTED_WITHOUT_EXTERNAL_CHECK",
            "approval_basis": "owner-accepted without external check (§27)",
            "data_complete": True,
        }
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        require_owner_approved_sanity(
            self.data_dir,
            symbol="GBPUSD",
            timeframe="M1",
            primary_sha256=primary.sha256,
        )

        report["status"] = "PENDING_OWNER_SPOTCHECK"
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not owner-approved"):
            require_owner_approved_sanity(
                self.data_dir,
                symbol="GBPUSD",
                timeframe="M1",
                primary_sha256=primary.sha256,
            )

        report["status"] = "OWNER_APPROVED"
        report["approval_basis"] = "unverified"
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "approval_basis"):
            require_owner_approved_sanity(
                self.data_dir,
                symbol="GBPUSD",
                timeframe="M1",
                primary_sha256=primary.sha256,
            )

        report["approval_basis"] = "owner-accepted without external check (§27)"
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.primary.open("a", encoding="utf-8") as stream:
            stream.write(
                "2026-01-01T00:01:00Z,1.25,1.4,1.2,1.35,dukascopy\n"
            )
        changed = load_candle_file(self.primary)
        with self.assertRaisesRegex(ValueError, "changed since its sanity check"):
            require_owner_approved_sanity(
                self.data_dir,
                symbol="GBPUSD",
                timeframe="M1",
                primary_sha256=changed.sha256,
            )

    def test_no_exact_timestamp_matches_is_not_approved(self):
        self.write(
            self.primary,
            [
                {"time": "2026-01-01T00:00:00Z", "open": "1.2", "high": "1.3", "low": "1.1", "close": "1.25", "source": "dukascopy"},
            ],
            ("time", "open", "high", "low", "close", "source"),
        )
        self.write(
            self.reference,
            [{"Datetime": "2026-01-01T01:00:00Z", "Close": "1.2501"}],
            ("Datetime", "Close"),
        )
        report = compare_candle_sources(
            self.primary,
            self.reference,
            symbol="GBPUSD",
            timeframe="M1",
            report_path=self.report_path,
        )
        self.assertEqual(report["status"], "NO_MATCHING_TIMESTAMPS")
        self.assertFalse(report["owner_approved"])

    def test_reference_timestamp_without_offset_is_rejected(self):
        self.write(
            self.primary,
            [
                {"time": "2026-01-01T00:00:00Z", "open": "1.2", "high": "1.3", "low": "1.1", "close": "1.25", "source": "dukascopy"},
            ],
            ("time", "open", "high", "low", "close", "source"),
        )
        self.write(
            self.reference,
            [{"Datetime": "2026-01-01 00:00:00", "Close": "1.25"}],
            ("Datetime", "Close"),
        )
        with self.assertRaisesRegex(ValueError, "include a UTC offset"):
            compare_candle_sources(
                self.primary,
                self.reference,
                symbol="GBPUSD",
                timeframe="M1",
                report_path=self.report_path,
            )


if __name__ == "__main__":
    unittest.main()
