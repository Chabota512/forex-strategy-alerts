import copy
import gzip
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from lab.backtest import (
    last_calendar_month_window,
    filter_candles_by_date_window,
    load_spec,
    main,
    run_pair,
    settings_fingerprint,
    validation_summary,
    write_alerts,
)
from lab.candle_data import CandleFileData
from lab.engine import Candle


SPEC_PATH = Path(__file__).parents[1] / "specs" / "seed_v1.5.0.json"


class DateWindowFilterTests(unittest.TestCase):
    def test_window_with_zero_candles_raises_clean_error(self):
        candles = [{"time": "2025-12-29T23:59:00Z"}]

        with self.assertRaisesRegex(ValueError, "No candles found in date window"):
            filter_candles_by_date_window(
                candles,
                date(2025, 12, 30),
                date(2025, 12, 31),
            )


class BacktestConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_spec(SPEC_PATH)

    def test_corrected_seed_has_confirmed_reward_and_cst_settings(self):
        self.assertEqual(self.spec["version"], "1.5.0")
        self.assertEqual(self.spec["cst"]["major_swing_neighbors"], 5)
        self.assertEqual(self.spec["cst"]["break_basis"], "close")
        self.assertEqual(self.spec["reward"]["lookahead_candles"], 12)
        self.assertEqual(self.spec["reward"]["thresholds"]["GBPUSD"]["d_min"], "0.0010")
        self.assertEqual(self.spec["reward"]["thresholds"]["USDJPY"]["a_max"], "0.10")
        self.assertTrue(self.spec["frozen"])
        self.assertEqual(self.spec["status"], "owner_approved_frozen")

    def test_gzip_alert_writer_round_trips_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "alerts.csv.gz"
            write_alerts(
                path,
                [{"time": "2026-01-01T00:00:00Z", "direction": "BUY"}],
            )
            with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
                rows = stream.read().splitlines()

        self.assertTrue(rows[0].startswith("time,symbol,tf,direction,"))
        self.assertEqual(len(rows), 2)
        self.assertIn("2026-01-01T00:00:00Z", rows[1])
        self.assertIn(",BUY,", rows[1])

    def test_validation_report_splits_reward_and_counts_by_strength(self):
        months = [f"2025-{month:02d}" for month in range(1, 13)]
        alerts = [
            {
                "time": "2025-10-01T00:00:00Z",
                "symbol": "GBPUSD",
                "tf": "M5",
                "period": "VALIDATION",
                "strength_label": "STRONG",
                "reward_status": "SCORED",
                "reward_score": "1.5",
            },
            {
                "time": "2025-10-02T00:00:00Z",
                "symbol": "GBPUSD",
                "tf": "M5",
                "period": "VALIDATION",
                "strength_label": "NEUTRAL",
                "reward_status": "INSUFFICIENT_LOOKAHEAD",
                "reward_score": "",
            },
            {
                "time": "2025-11-01T00:00:00Z",
                "symbol": "USDJPY",
                "tf": "M15",
                "period": "VALIDATION",
                "strength_label": "WEAK",
                "reward_status": "SCORED",
                "reward_score": "-1",
            },
        ]

        report = validation_summary(
            alerts,
            spec=self.spec,
            months=months,
            missing_months=[],
        )
        strata = report["strength_label_stratified"]

        self.assertEqual(strata["overall"]["STRONG"]["validation_alerts"], 1)
        self.assertEqual(
            strata["overall"]["STRONG"]["total_validation_reward"], "1.5"
        )
        self.assertEqual(strata["overall"]["NEUTRAL"]["validation_alerts"], 1)
        self.assertEqual(
            strata["overall"]["NEUTRAL"]["validation_alerts_insufficient_lookahead"],
            1,
        )
        self.assertEqual(
            strata["overall"]["NEUTRAL"]["total_validation_reward"], "0"
        )
        self.assertEqual(strata["overall"]["WEAK"]["total_validation_reward"], "-1")
        self.assertEqual(
            strata["by_symbol_timeframe"]["GBPUSD_M5"]["NEUTRAL"]["validation_alerts"],
            1,
        )

    def test_settings_fingerprint_changes_when_scoring_or_cst_setting_changes(self):
        original = settings_fingerprint(self.spec)
        changed = copy.deepcopy(self.spec)
        changed["reward"]["same_candle_tie"] = "target_first"
        self.assertNotEqual(original, settings_fingerprint(changed))
        changed = copy.deepcopy(self.spec)
        changed["cst"]["break_basis"] = "wick"
        self.assertNotEqual(original, settings_fingerprint(changed))

    def test_loader_contract_and_alert_rows_record_reproducibility_settings(self):
        candles = [
            Candle.from_values(
                "2026-01-01T00:00:00Z",
                "1.2000",
                "1.3000",
                "1.1000",
                "1.2500",
            ),
            Candle.from_values(
                "2026-01-01T00:01:00Z",
                "1.2500",
                "1.3100",
                "1.2000",
                "1.2400",
            ),
        ]
        candles.extend(
            Candle.from_values(
                f"2026-01-01T00:{index:02d}:00Z",
                "1.2400",
                "1.2402",
                "1.2390",
                "1.2400",
            )
            for index in range(2, 14)
        )
        fingerprint = settings_fingerprint(self.spec)
        alert_rows = run_pair(
            candles,
            symbol="GBPUSD",
            timeframe="M5",
            source="synthetic",
            input_sha256="a" * 64,
            spec=self.spec,
            months=[],
            run_id="run-test",
            config_fingerprint=fingerprint,
        )
        self.assertEqual(len(alert_rows), 1)
        alert = alert_rows[0]
        self.assertEqual(alert["direction"], "SELL")
        self.assertEqual(alert["reward_score"], "1.5")
        self.assertEqual(alert["reward_status"], "SCORED")
        self.assertEqual(alert["spec_version"], self.spec["version"])
        self.assertEqual(alert["settings_fingerprint"], fingerprint)
        self.assertEqual(json.loads(alert["cst_settings_json"]), self.spec["cst"])
        self.assertEqual(json.loads(alert["reward_settings_json"]), self.spec["reward"])


class ValidationWindowTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_spec(SPEC_PATH)
        self.spec["instruments"] = ["GBPUSD"]
        self.spec["timeframes"] = ["M5"]
        self.months = [
            "2025-11",
            "2025-12",
            "2026-01",
            "2026-02",
            "2026-03",
            "2026-04",
            "2026-05",
            "2026-06",
            "2026-07",
            "2026-08",
            "2026-09",
            "2026-10",
        ]

    def test_last_12_calendar_months_are_selected_and_gaps_reported(self):
        records = [
            {"time": f"{month}-15T00:00:00Z", "open": Decimal("1"), "high": Decimal("1"), "low": Decimal("1"), "close": Decimal("1")}
            for month in self.months
        ]
        data = CandleFileData(records, Path("sample.csv"), "synthetic", "b" * 64)
        months, missing = last_calendar_month_window({("GBPUSD", "M5"): data}, 12)
        self.assertEqual(months, self.months)
        self.assertEqual(missing, [])

        records.pop(4)
        data = CandleFileData(records, Path("sample.csv"), "synthetic", "b" * 64)
        _, missing = last_calendar_month_window({("GBPUSD", "M5"): data}, 12)
        self.assertEqual(missing, ["2026-03"])

    def test_objective_uses_validation_scores_only_and_skips_unscored_alerts(self):
        alerts = []
        for index, month in enumerate(self.months):
            period = "TRAIN" if index < 9 else "VALIDATION"
            score = "9" if period == "TRAIN" else ("0.5" if index == 9 else "1.5")
            status = "SCORED"
            if index == 11:
                score = ""
                status = "INSUFFICIENT_LOOKAHEAD"
            alerts.append(
                {
                    "time": f"{month}-15T00:00:00Z",
                    "symbol": "GBPUSD",
                    "tf": "M5",
                    "period": period,
                    "reward_score": score,
                    "reward_status": status,
                }
            )
        summary = validation_summary(
            alerts,
            spec=self.spec,
            months=self.months,
            missing_months=[],
        )
        self.assertEqual(summary["total_validation_reward"], "2")
        self.assertEqual(summary["validation_alerts"], 3)
        self.assertEqual(summary["validation_alerts_scored"], 2)
        self.assertEqual(summary["validation_alerts_insufficient_lookahead"], 1)
        self.assertEqual(summary["month_coverage"]["covered_count"], 12)
        self.assertEqual(summary["pair_floors"]["GBPUSD_M5"]["floor_status"], "FAIL")

    def test_incomplete_calendar_window_never_reports_validation_score(self):
        summary = validation_summary(
            [],
            spec=self.spec,
            months=self.months,
            missing_months=["2026-03"],
        )
        self.assertEqual(summary["status"], "INSUFFICIENT_12_CONSECUTIVE_MONTHS")
        self.assertIsNone(summary["total_validation_reward"])
        self.assertEqual(summary["all_floors_status"], "NOT_EVALUATED")
        self.assertEqual(summary["pair_floors"]["GBPUSD_M5"]["floor_status"], "NOT_EVALUATED")

    def test_summary_only_cli_reports_split_and_synthetic_warning_without_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_dir = root / "data"
            data_dir.mkdir()
            spec = copy.deepcopy(self.spec)
            spec["instruments"] = ["GBPUSD"]
            spec["timeframes"] = ["M5"]
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            data_path = data_dir / "GBPUSD_M5.csv"
            rows = ["time,open,high,low,close,source"]
            for month in self.months:
                rows.extend(
                    [
                        f"{month}-01T00:00:00Z,1.1000,1.1005,1.0998,1.1003,synthetic",
                        f"{month}-01T00:05:00Z,1.1003,1.1010,1.0999,1.1000,synthetic",
                        f"{month}-01T00:10:00Z,1.1000,1.1002,1.0998,1.1000,synthetic",
                    ]
                )
            data_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

            alert_path = root / "alerts.csv"
            report_path = root / "report.json"
            captured = io.StringIO()
            with redirect_stdout(captured):
                result = main(
                    [
                        "--spec",
                        str(spec_path),
                        "--data-dir",
                        str(data_dir),
                        "--output",
                        str(alert_path),
                        "--report",
                        str(report_path),
                        "--summary-only",
                    ]
                )

            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertFalse(alert_path.exists())
        self.assertIn("SYNTHETIC DATA ONLY", captured.getvalue())
        self.assertEqual(report["spec"]["version"], self.spec["version"])
        self.assertEqual(report["spec"]["effective_settings"]["cst"], self.spec["cst"])
        self.assertFalse(report["alert_log"]["written"])
        self.assertIsNone(report["alert_log"]["file"])
        self.assertEqual(report["validation"]["status"], "READY")
        self.assertEqual(len(report["validation"]["training_months"]), 9)
        self.assertEqual(len(report["validation"]["validation_months"]), 3)
        self.assertEqual(report["validation"]["training"]["alerts"], 9)
        self.assertEqual(
            report["validation"]["validation_alerts_insufficient_lookahead"],
            3,
        )
        self.assertEqual(report["validation"]["all_floors_status"], "FAIL")

    def test_summary_only_cli_skips_missing_pairs_and_lists_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_dir = root / "data"
            data_dir.mkdir()
            spec_path = SPEC_PATH

            for timeframe in ("M5", "M15"):
                data_path = data_dir / f"XAUUSD_{timeframe}.csv"
                rows = ["time,open,high,low,close,source"]
                for month in range(1, 13):
                    rows.append(
                        f"2025-{month:02d}-01T00:00:00Z,"
                        "2000.0,2001.0,1999.0,2000.5,synthetic"
                    )
                data_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

            report_path = root / "report.json"
            captured = io.StringIO()
            with redirect_stdout(captured):
                result = main(
                    [
                        "--spec",
                        str(spec_path),
                        "--data-dir",
                        str(data_dir),
                        "--report",
                        str(report_path),
                        "--summary-only",
                    ]
                )

            report = json.loads(report_path.read_text(encoding="utf-8"))

        expected_skips = {
            ("GBPUSD", "M5"),
            ("GBPUSD", "M15"),
            ("USDJPY", "M5"),
            ("USDJPY", "M15"),
        }
        actual_skips = {
            (item["symbol"], item["timeframe"])
            for item in report["skipped_pairs"]
        }
        self.assertEqual(result, 0)
        self.assertEqual(actual_skips, expected_skips)
        self.assertTrue(
            all(
                report["validation"]["pair_floors"][
                    f"{symbol}_{timeframe}"
                ]["pair_status"]
                == "SKIPPED"
                for symbol, timeframe in expected_skips
            )
        )
        self.assertIn("Skipped pair: GBPUSD M5", captured.getvalue())
        self.assertIn("Skipped pair: USDJPY M15", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
