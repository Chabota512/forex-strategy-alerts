from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lab.candle_data import CandleFileData, DEFAULT_DATA_DIR, load_candles_with_metadata
from lab.data_sanity import require_owner_approved_derived_timeframe
from lab.evolve import DEFAULT_DATA_ROOT, load_approved_market_data
from lab.populate_snapshots import DEFAULT_INPUT
from lab.resample import main as resample_main
from lab.server import DATA_PATH, LabService


class MonthlySourcePathTests(unittest.TestCase):
    def test_gold_candle_loader_never_resolves_top_level_m1_or_derived_csvs(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            loaded = CandleFileData(
                candles=[
                    {
                        "time": "2023-01-01T00:00:00Z",
                        "open": 2000.0,
                        "high": 2001.0,
                        "low": 1999.0,
                        "close": 2000.5,
                    }
                ],
                path=data_root / "monthly.csv",
                source="dukascopy",
                sha256="monthly-hash",
            )
            for timeframe in ("M1", "M5", "M15"):
                (data_root / "XAUUSD" / timeframe).mkdir(parents=True)
                with (
                    patch(
                        "lab.candle_data.resolve_data_file",
                        side_effect=AssertionError(
                            "monthly gold must not resolve lab/data/*_M1.csv"
                        ),
                    ),
                    patch(
                        "lab.monthly_data.load_monthly_timeframe",
                        return_value=loaded,
                    ) as load_monthly,
                ):
                    result = load_candles_with_metadata(
                        "XAUUSD",
                        timeframe,
                        data_dir=data_root,
                    )
                self.assertIs(result, loaded)
                load_monthly.assert_called_once_with(data_root, "XAUUSD", timeframe)

    def test_api_monthly_path_never_falls_back_to_reading_a_directory_as_csv(self):
        self.assertEqual(DATA_PATH, DEFAULT_DATA_DIR / "XAUUSD" / "M1")
        self.assertEqual(DEFAULT_INPUT, DEFAULT_DATA_DIR)
        with tempfile.TemporaryDirectory() as temporary:
            monthly_path = Path(temporary) / "XAUUSD" / "M1"
            monthly_path.mkdir(parents=True)
            with (
                patch(
                    "lab.server.load_monthly_manifest",
                    side_effect=ValueError("manifest missing"),
                ),
                patch(
                    "lab.server._read_m1_file",
                    side_effect=AssertionError(
                        "a monthly directory must never be read as one merged CSV"
                    ),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "manifest missing"):
                    LabService(data_path=monthly_path)

    def test_gold_derived_validation_does_not_resolve_merged_m1_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            manifest = data_root / "XAUUSD" / "monthly-manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.touch()
            with patch(
                "lab.data_sanity.sha256_file",
                side_effect=AssertionError("must not hash a top-level merged M1"),
            ):
                with self.assertRaisesRegex(ValueError, "monthly dataset"):
                    require_owner_approved_derived_timeframe(
                        data_root,
                        symbol="XAUUSD",
                        timeframe="M15",
                    )

    def test_resample_all_never_scans_top_level_m1_csvs(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            (data_root / "XAUUSD_M1.csv").write_text("unexpected", encoding="utf-8")
            (data_root / "GBPUSD_M1.csv").write_text("unexpected", encoding="utf-8")
            manifest = data_root / "XAUUSD" / "monthly-manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}", encoding="utf-8")
            rebuild_result = {
                "symbol": "XAUUSD",
                "source_sha256": "verified-monthly-source",
                "month_count": 36,
                "files_written": 0,
                "files_unchanged": 216,
                "timeframe_counts": {"M1": 36, "M5": 36},
            }
            with patch(
                "lab.resample.resample_file",
                side_effect=AssertionError(
                    "--all must not resolve merged top-level M1 files"
                ),
            ), patch(
                "lab.monthly_data.regenerate_monthly_outputs",
                return_value=rebuild_result,
            ) as regenerate:
                self.assertEqual(
                    resample_main(["--all", "--data-dir", str(data_root)]),
                    0,
                )
            regenerate.assert_called_once_with(data_root, output_root=data_root)

    def test_evolution_default_and_loader_use_the_monthly_root(self):
        self.assertEqual(DEFAULT_DATA_ROOT, DEFAULT_DATA_DIR)
        self.assertEqual(DEFAULT_INPUT, DEFAULT_DATA_DIR)
        self.assertEqual(
            inspect.signature(load_approved_market_data).parameters[
                "data_root"
            ].default,
            DEFAULT_DATA_DIR,
        )
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            (data_root / "XAUUSD" / "M1").mkdir(parents=True)
            manifest = {"source": "dukascopy", "months": ["2023-01"]}
            timeframe_data = CandleFileData(
                candles=[
                    {
                        "time": "2023-01-01T00:00:00Z",
                        "open": 2000.0,
                        "high": 2001.0,
                        "low": 1999.0,
                        "close": 2000.5,
                    }
                ],
                path=data_root / "XAUUSD" / "M1" / "2023-01.csv",
                source="dukascopy",
                sha256="monthly-hash",
            )
            loop_window = SimpleNamespace(
                all_months=["2023-01"],
                profile="test",
            )
            with (
                patch("lab.evolve.load_loop_window", return_value=loop_window),
                patch("lab.evolve.load_monthly_manifest", return_value=manifest),
                patch("lab.evolve.verify_monthly_gate", return_value="monthly-hash"),
                patch(
                    "lab.evolve.load_monthly_timeframe",
                    return_value=timeframe_data,
                ),
                patch(
                    "lab.evolve.load_candle_file",
                    side_effect=AssertionError(
                        "evolution must not load a top-level XAUUSD_M1.csv"
                    ),
                ),
            ):
                result = load_approved_market_data(data_root)
            self.assertEqual(result.source, "dukascopy")
            self.assertEqual(result.records_by_timeframe["M5"], timeframe_data.candles)


if __name__ == "__main__":
    unittest.main()
