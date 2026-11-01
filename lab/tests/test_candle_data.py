import csv
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from lab.candle_data import (
    load_candles,
    load_candles_with_metadata,
    load_candle_file,
    resolve_data_file,
)
from lab.resample import _aggregate


class CandleDataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_csv(self, name, rows, fields=("time", "open", "high", "low", "close")):
        path = self.data_dir / name
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def valid_row(self, time_value="2026-01-01T00:00:00+02:00", source=None):
        row = {
            "time": time_value,
            "open": "1.2",
            "high": "1.3",
            "low": "1.1",
            "close": "1.25",
        }
        if source is not None:
            row["source"] = source
        return row

    def test_public_loader_maps_symbol_and_timeframe_to_local_csv(self):
        self.write_csv("GBPUSD_M1.csv", [self.valid_row()])
        with patch("lab.candle_data.DEFAULT_DATA_DIR", self.data_dir):
            records = load_candles("gbpusd", "m1")
        loaded = load_candles_with_metadata("gbpusd", "m1", data_dir=self.data_dir)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["time"], "2025-12-31T22:00:00Z")
        self.assertEqual(records[0]["open"], Decimal("1.2"))
        self.assertEqual(loaded.source, "unverified_real")
        self.assertTrue(loaded.sha256)

    def test_canonical_real_file_takes_precedence_over_synthetic_file(self):
        real = self.write_csv("GBPUSD_M5.csv", [self.valid_row(source="dukascopy")],
                              fields=("time", "open", "high", "low", "close", "source"))
        synthetic = self.write_csv(
            "synthetic_GBPUSD_M5.csv",
            [self.valid_row(source="synthetic")],
            fields=("time", "open", "high", "low", "close", "source"),
        )
        self.assertEqual(resolve_data_file(self.data_dir, "GBPUSD", "M5"), real)
        self.assertNotEqual(real, synthetic)
        self.assertEqual(
            load_candle_file(real).source,
            "dukascopy",
        )

    def test_loader_normalizes_supported_timestamp_headers_for_resampling(self):
        expected_times = [
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:01:00Z",
            "2026-01-01T00:02:00Z",
        ]
        for header in ("time", "timestamp", "timestamp_utc"):
            with self.subTest(header=header):
                rows = []
                for index, time_value in enumerate(expected_times):
                    if header == "timestamp_utc":
                        time_value = time_value.removesuffix("Z")
                    rows.append(
                        {
                            header: time_value,
                            "open": "1.2",
                            "high": "1.3",
                            "low": "1.1",
                            "close": f"1.2{index}",
                            "source": "Dukascopy",
                        }
                    )
                path = self.write_csv(
                    f"{header}.csv",
                    rows,
                    fields=(header, "open", "high", "low", "close", "source"),
                )

                loaded = load_candle_file(path)
                self.assertEqual(
                    [candle["time"] for candle in loaded.candles], expected_times
                )
                self.assertEqual(
                    [
                        candle["timestamp"].isoformat()
                        for candle in loaded.candles
                    ],
                    [time.replace("Z", "+00:00") for time in expected_times],
                )
                self.assertEqual(
                    {candle["source"] for candle in loaded.candles}, {"dukascopy"}
                )
                resampled, incomplete = _aggregate(loaded.candles, 1)
                self.assertEqual(len(resampled), 3)
                self.assertEqual(incomplete, 0)

    def test_legacy_synthetic_filename_is_not_resolved_from_real_data_dir(self):
        self.write_csv("synthetic_GBPUSD_M5.csv", [self.valid_row(source="synthetic")],
                       fields=("time", "open", "high", "low", "close", "source"))
        with self.assertRaisesRegex(FileNotFoundError, "GBPUSD_M5.csv"):
            resolve_data_file(self.data_dir, "GBPUSD", "M5")

    def test_synthetic_filename_supplies_synthetic_provenance(self):
        path = self.write_csv("synthetic_XAUUSD_M15.csv", [self.valid_row()])
        self.assertEqual(load_candle_file(path).source, "synthetic")

    def test_missing_columns_are_rejected(self):
        path = self.write_csv("bad.csv", [{"time": "2026-01-01T00:00:00Z", "close": "1"}],
                              fields=("time", "close"))
        with self.assertRaisesRegex(ValueError, "missing columns"):
            load_candle_file(path)

    def test_empty_file_is_rejected(self):
        path = self.write_csv("empty.csv", [])
        with self.assertRaisesRegex(ValueError, "no candles"):
            load_candle_file(path)

    def test_duplicate_and_out_of_order_times_are_rejected(self):
        rows = [
            self.valid_row("2026-01-01T00:00:00Z"),
            self.valid_row("2026-01-01T00:00:00+00:00"),
        ]
        path = self.write_csv("duplicates.csv", rows)
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            load_candle_file(path)

    def test_invalid_ohlc_is_rejected(self):
        row = self.valid_row()
        row["high"] = "1.0"
        path = self.write_csv("invalid.csv", [row])
        with self.assertRaisesRegex(ValueError, "High is below"):
            load_candle_file(path)

    def test_inconsistent_source_labels_are_rejected(self):
        rows = [
            self.valid_row("2026-01-01T00:00:00Z", "dukascopy"),
            self.valid_row("2026-01-01T00:01:00Z", "yahoo"),
        ]
        path = self.write_csv(
            "mixed.csv",
            rows,
            fields=("time", "open", "high", "low", "close", "source"),
        )
        with self.assertRaisesRegex(ValueError, "source labels"):
            load_candle_file(path)

    def test_symbol_and_timeframe_cannot_escape_data_directory(self):
        with self.assertRaisesRegex(ValueError, "only letters and numbers"):
            resolve_data_file(self.data_dir, "../secret", "M1")


if __name__ == "__main__":
    unittest.main()
