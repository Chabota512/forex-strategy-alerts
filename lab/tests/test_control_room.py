import csv
import gzip
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from http.server import ThreadingHTTPServer
from contextlib import redirect_stdout

from lab.backtest import load_spec, run_smoke_backtest, run_smoke_window
from lab.candle_data import CandleFileData, sha256_file
from lab.data_sanity import sanity_manifest_path
from lab.engine import Candle
from lab.resample import resample_file
from lab.server import (
    GEMINI_SYSTEM_PROMPT,
    LabService,
    RequestError,
    _gemini_comment,
    make_handler,
)

SPEC_PATH = Path(__file__).parents[1] / "specs" / "seed_v1.5.0.json"


def write_m1(path: Path, minutes: int, *, source: str = "dukascopy") -> None:
    start = datetime(2025, 12, 30, tzinfo=timezone.utc)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["time", "open", "high", "low", "close", "source"],
        )
        writer.writeheader()
        for index in range(minutes):
            opened = Decimal("2600") + Decimal(index % 10) / Decimal(100)
            closed = opened + (Decimal("0.10") if index % 2 == 0 else Decimal("-0.10"))
            writer.writerow(
                {
                    "time": (start + timedelta(minutes=index)).isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "open": format(opened, "f"),
                    "high": format(max(opened, closed) + Decimal("0.20"), "f"),
                    "low": format(min(opened, closed) - Decimal("0.20"), "f"),
                    "close": format(closed, "f"),
                    "source": source,
                }
            )


def example_alert() -> dict:
    return {
        "alertId": "fixture-alert",
        "time": "2025-12-30T00:05:00Z",
        "symbol": "XAUUSD",
        "timeframe": "M5",
        "direction": "SELL",
        "cct": "ranging",
        "cst": "undefined",
        "strengthLabel": "NEUTRAL",
        "note": None,
        "candle": {
            "time": "2025-12-30T00:05:00Z",
            "open": 2600.1,
            "high": 2600.4,
            "low": 2600.0,
            "close": 2600.0,
        },
        "previousCandle": {
            "time": "2025-12-30T00:00:00Z",
            "open": 2600.0,
            "high": 2600.3,
            "low": 2599.8,
            "close": 2600.1,
        },
        "scored": False,
    }


def smoke_fixture():
    first_time = datetime(2025, 12, 30, tzinfo=timezone.utc)
    raw = [
        ("2000", "2002", "1999", "2001"),
        ("2001", "2003", "1998", "2000"),
        ("2000", "2001", "1997", "1999"),
        ("1999", "2002", "1996", "2000"),
    ]
    records = []
    for index, values in enumerate(raw):
        timestamp = (
            first_time + timedelta(minutes=index * 5)
        ).isoformat().replace("+00:00", "Z")
        candle = Candle.from_values(timestamp, *values)
        records.append(
            {
                "time": candle.time,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
            }
        )
    loaded = {
        ("XAUUSD", "M5"): CandleFileData(
            candles=records,
            path=Path("approved-input.csv"),
            source="dukascopy",
            sha256="a" * 64,
        )
    }
    return loaded, first_time.date()


class ControlRoomServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_path = self.root / "XAUUSD_M1.csv"
        self.output_dir = self.root / "output"
        write_m1(self.data_path, 45)
        reference_path = self.root / "reference.csv"
        reference_path.write_text(
            "time,close\n2025-12-30T00:00:00Z,2600.10\n",
            encoding="utf-8",
        )
        manifest_path = sanity_manifest_path(self.root, "XAUUSD", "M1")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "status": "OWNER_APPROVED",
                    "owner_approved": True,
                    "symbol": "XAUUSD",
                    "timeframe": "M1",
                    "primary_sha256": sha256_file(self.data_path),
                    "reference_file": str(reference_path),
                    "reference_sha256": sha256_file(reference_path),
                }
            ),
            encoding="utf-8",
        )
        resample_file(self.data_path, output_dir=self.root)
        self.ai_calls = 0

        def fake_ai(alert, api_key):
            self.ai_calls += 1
            self.assertEqual(api_key, "test-gemini-key")
            self.assertEqual(alert["alertId"], "fixture-alert")
            return (
                "The preceding candle shows a small range expansion. "
                "Trend context remains mixed.",
                {"promptTokens": 40, "responseTokens": 18, "totalTokens": 58},
            )

        self.service = LabService(
            data_path=self.data_path,
            spec_path=SPEC_PATH,
            output_dir=self.output_dir,
            ai_generator=fake_ai,
        )
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.service)
        )
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request_json(self, path: str, *, method: str = "GET", payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": "application/json"} if body is not None else {},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def _approve_registered_symbol(self, symbol: str) -> Path:
        data_path = self.root / f"{symbol}_M1.csv"
        write_m1(data_path, 45)
        reference_path = self.root / f"{symbol}_reference.csv"
        reference_path.write_text(
            "time,close\n2025-12-30T00:00:00Z,2600.10\n",
            encoding="utf-8",
        )
        manifest_path = sanity_manifest_path(self.root, symbol, "M1")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "status": "OWNER_APPROVED",
                    "owner_approved": True,
                    "symbol": symbol,
                    "timeframe": "M1",
                    "primary_sha256": sha256_file(data_path),
                    "reference_file": str(reference_path),
                    "reference_sha256": sha256_file(reference_path),
                }
            ),
            encoding="utf-8",
        )
        resample_file(data_path, output_dir=self.root)
        return data_path

    def test_dataset_endpoint_reports_real_source_and_approved_timeframes(self):
        status, result = self.request_json("/api/lab/dataset")

        self.assertEqual(status, 200)
        self.assertEqual(result["symbol"], "XAUUSD")
        self.assertEqual(result["source"], "dukascopy")
        self.assertEqual(result["candleCount"], 45)
        self.assertEqual(result["defaultReviewTimeframes"], ["M5", "M15"])
        metrics = result["ownerAttestedSanityMetrics"]
        self.assertEqual(metrics["provenance"], "owner_reported")
        self.assertEqual(metrics["levelCorrelation"], 0.999459)
        self.assertEqual(metrics["hourlyChangeCorrelation"], 0.962553)
        self.assertEqual(metrics["medianFuturesOverSpotOffsetUsd"], 15.895)
        self.assertIn("local raw archive", metrics["note"])
        self.assertEqual(
            result["m1ReviewNotice"],
            "M1 fires on ~1 in 4 candles — for engine checks only, not human review.",
        )

    def test_registered_symbol_flows_through_dataset_candles_replay_and_backtest(self):
        data_path = self._approve_registered_symbol("GBPUSD")

        dataset_status, dataset = self.request_json("/api/lab/dataset?symbol=GBPUSD")
        self.assertEqual(dataset_status, 200)
        self.assertEqual(dataset["symbol"], "GBPUSD")
        self.assertTrue(dataset["ownerApproved"])
        self.assertEqual(dataset["simulationCost"], 0.0001)
        self.assertEqual(dataset["sourceSha256"], sha256_file(data_path))

        query = urllib.parse.urlencode(
            {
                "symbol": "GBPUSD",
                "timeframe": "M5",
                "from": "2025-12-30",
                "to": "2025-12-30",
            }
        )
        candles_status, candles = self.request_json(f"/api/lab/candles?{query}")
        self.assertEqual(candles_status, 200)
        self.assertEqual(candles["symbol"], "GBPUSD")
        self.assertEqual(candles["totalCandles"], 9)
        self.assertEqual(candles["sourceSha256"], sha256_file(data_path))

        replay_status, replay = self.request_json(
            "/api/lab/replay/sessions",
            method="POST",
            payload={
                "symbol": "GBPUSD",
                "timeframe": "M5",
                "from": "2025-12-30",
                "to": "2025-12-30",
            },
        )
        self.assertEqual(replay_status, 201)
        self.assertEqual(replay["symbol"], "GBPUSD")
        self.assertEqual(replay["totalCandles"], 9)

        backtest_status, backtest_result = self.request_json(
            "/api/lab/backtest",
            method="POST",
            payload={
                "symbol": "GBPUSD",
                "timeframe": "M5",
                "from": "2025-12-30",
                "to": "2025-12-30",
            },
        )
        self.assertEqual(backtest_status, 200)
        self.assertEqual(backtest_result["symbol"], "GBPUSD")
        self.assertEqual(backtest_result["status"], "SMOKE_NOT_SCORED")

    def test_candle_endpoint_resamples_and_reports_truncation(self):
        query = urllib.parse.urlencode(
            {
                "timeframe": "M5",
                "from": "2025-12-30",
                "to": "2025-12-30",
                "limit": "4",
            }
        )
        status, result = self.request_json(f"/api/lab/candles?{query}")

        self.assertEqual(status, 200)
        self.assertEqual(result["timeframe"], "M5")
        self.assertEqual(result["totalCandles"], 9)
        self.assertEqual(len(result["candles"]), 4)
        self.assertTrue(result["truncated"])
        self.assertIn("cct", result["candles"][0])
        self.assertIn("cst", result["candles"][0])
        self.assertEqual(result["sourceSha256"], self.service.source_sha256)

    def test_m1_candle_endpoint_returns_one_month_with_full_window_indexes(self):
        records = []
        for timestamp in (
            "2025-01-31T23:58:00Z",
            "2025-02-01T00:00:00Z",
            "2025-02-01T00:01:00Z",
            "2025-03-01T00:00:00Z",
        ):
            records.append(
                {
                    "time": timestamp,
                    "open": Decimal("2600"),
                    "high": Decimal("2601"),
                    "low": Decimal("2599"),
                    "close": Decimal("2600"),
                }
            )
        self.service.data_by_timeframe["M1"] = CandleFileData(
            candles=records,
            path=self.data_path,
            source="dukascopy",
            sha256=self.service.source_sha256,
        )
        base_query = {
            "timeframe": "M1",
            "from": "2025-01-31",
            "to": "2025-03-01",
        }
        query = urllib.parse.urlencode({**base_query, "chunkMonth": "2025-02"})
        status, result = self.request_json(f"/api/lab/candles?{query}")

        self.assertEqual(status, 200)
        self.assertEqual(result["totalCandles"], 4)
        self.assertEqual(result["chunkMonth"], "2025-02")
        self.assertEqual(result["chunkOffset"], 0)
        self.assertEqual(
            result["chunks"],
            [
                {"month": "2025-01", "startIndex": 0, "count": 1},
                {"month": "2025-02", "startIndex": 1, "count": 2},
                {"month": "2025-03", "startIndex": 3, "count": 1},
            ],
        )
        self.assertEqual(
            [candle["time"] for candle in result["candles"]],
            [
                "2025-01-31T23:58:00Z",
                "2025-02-01T00:00:00Z",
                "2025-02-01T00:01:00Z",
            ],
        )
        self.assertFalse(result["truncated"])

        default_query = urllib.parse.urlencode(base_query)
        _, default_result = self.request_json(f"/api/lab/candles?{default_query}")
        self.assertEqual(default_result["chunkMonth"], "2025-03")
        self.assertEqual(len(default_result["candles"]), 4)
        self.assertEqual(default_result["candles"][-1]["time"], "2025-03-01T00:00:00Z")
        self.assertEqual(default_result["totalCandles"], 4)

    def test_incremental_session_streams_variant_b_pressure_and_engulfing(self):
        records = [
            {
                "time": "2025-12-30T00:00:00Z",
                "open": Decimal("11"),
                "high": Decimal("12"),
                "low": Decimal("9"),
                "close": Decimal("10"),
            },
            {
                "time": "2025-12-30T00:05:00Z",
                "open": Decimal("10"),
                "high": Decimal("12.5"),
                "low": Decimal("9.2"),
                "close": Decimal("9.5"),
            },
            {
                "time": "2025-12-30T00:10:00Z",
                "open": Decimal("9.5"),
                "high": Decimal("10.2"),
                "low": Decimal("9.0"),
                "close": Decimal("9.8"),
            },
        ]
        self.service.data_by_timeframe["M5"] = CandleFileData(
            candles=records,
            path=self.data_path,
            source="dukascopy",
            sha256=self.service.source_sha256,
        )

        base_payload = {
            "timeframe": "M5",
            "from": "2025-12-30",
            "to": "2025-12-30",
        }
        _, default_start = self.request_json(
            "/api/lab/replay/sessions",
            method="POST",
            payload=base_payload,
        )
        _, default_advance = self.request_json(
            f"/api/lab/replay/sessions/{default_start['sessionId']}/advance",
            method="POST",
            payload={"toIndex": 1, "viewStart": 0, "viewEnd": 1},
        )
        self.assertFalse(
            any(
                item["alert"]["alertType"].endswith("-B")
                for item in default_advance["newAlerts"]
            )
        )

        status, started = self.request_json(
            "/api/lab/replay/sessions",
            method="POST",
            payload={**base_payload, "variant_b": True},
        )
        self.assertEqual(status, 201)
        self.assertEqual(started["wickPressureStates"], [{"index": 0, "state": "balanced"}])
        self.assertEqual(started["currentCandle"]["wickPressure"], "balanced")

        _, advanced = self.request_json(
            f"/api/lab/replay/sessions/{started['sessionId']}/advance",
            method="POST",
            payload={"toIndex": 1, "viewStart": 0, "viewEnd": 1},
        )
        self.assertEqual(
            advanced["newWickPressureStates"],
            [{"index": 1, "state": "down-pressure"}],
        )
        alert = advanced["newAlerts"][0]["alert"]
        self.assertEqual(alert["alertType"], "SELL-B")
        self.assertTrue(alert["engulfing"])
        self.assertEqual(advanced["newlyOpenedTrades"], [])
        self.assertEqual(advanced["newlyResolvedTrades"], [])
        self.assertEqual(advanced["simulation"]["index"], 1)

        _, entered = self.request_json(
            f"/api/lab/replay/sessions/{started['sessionId']}/advance",
            method="POST",
            payload={"toIndex": 2, "viewStart": 0, "viewEnd": 2},
        )
        self.assertEqual(len(entered["newlyOpenedTrades"]), 1)
        self.assertEqual(entered["newlyOpenedTrades"][0]["entryIndex"], 2)
        self.assertEqual(entered["newlyOpenedTrades"][0]["alertType"], "SELL-B")
        self.assertEqual(entered["newlyResolvedTrades"], [])
        self.assertEqual(entered["simulation"]["openTradeCount"], 1)
        self.assertEqual(len(entered["simulation"]["equityCurve"]), 3)

    def test_backtest_endpoint_runs_unscored_smoke_path_without_slice_files(self):
        status, result = self.request_json(
            "/api/lab/backtest",
            method="POST",
            payload={
                "timeframe": "M5",
                "from": "2025-12-30",
                "to": "2025-12-30",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "SMOKE_NOT_SCORED")
        self.assertEqual(result["specVersion"], "1.5.0")
        self.assertEqual(
            result["ownerAttestedSanityMetrics"]["provenance"], "owner_reported"
        )
        self.assertEqual(len(result["candles"]), 9)
        self.assertFalse(result["truncated"])
        self.assertIn("cct", result["candles"][0])
        self.assertIn("cst", result["candles"][0])
        self.assertTrue(all(alert["scored"] is False for alert in result["alerts"]))
        self.assertEqual(result["simulation"]["settings"]["startingBalance"], 10000)
        self.assertIn("simulatedTrades", result)
        snapshot_path = self.output_dir / "snapshots.jsonl"
        self.assertTrue(snapshot_path.is_file())
        snapshot_rows = [
            json.loads(line)
            for line in snapshot_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(snapshot_rows), len(result["alerts"]))
        self.assertEqual(
            {path.name for path in self.output_dir.iterdir()}, {"snapshots.jsonl"}
        )

    def test_backtest_rejects_windows_over_the_chart_cap(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        many_candles = [
            {
                "time": (start + timedelta(minutes=index)).isoformat().replace(
                    "+00:00", "Z"
                ),
                "open": Decimal("2600"),
                "high": Decimal("2601"),
                "low": Decimal("2599"),
                "close": Decimal("2600"),
            }
            for index in range(100_001)
        ]
        self.service.data_by_timeframe["M1"] = CandleFileData(
            candles=many_candles,
            path=self.data_path,
            source="dukascopy",
            sha256=self.service.source_sha256,
        )

        with self.assertRaisesRegex(RequestError, "more than 100,000 candles"):
            self.service.backtest_window("M1", "2025-01-01", "2026-01-01")

    def test_pending_spotcheck_blocks_replay_and_simulation(self):
        manifest_path = sanity_manifest_path(self.root, "XAUUSD", "M1")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "status": "PENDING_OWNER_SPOTCHECK",
                "owner_approved": False,
                "review_method": "OWNER_SPOTCHECK",
                "data_complete": True,
            }
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(RequestError, "not owner-approved"):
            self.service.create_replay_session(
                "M1", "2025-12-30", "2025-12-31"
            )
        with self.assertRaisesRegex(RequestError, "not owner-approved"):
            self.service.backtest_window(
                "M1", "2025-12-30", "2025-12-31"
            )

    def test_synthetic_source_is_rejected(self):
        synthetic_dir = self.root / "synthetic"
        synthetic_dir.mkdir()
        synthetic_path = synthetic_dir / "XAUUSD_M1.csv"
        write_m1(synthetic_path, 5, source="synthetic")

        with self.assertRaisesRegex(ValueError, "approved Dukascopy"):
            LabService(
                data_path=synthetic_path,
                spec_path=SPEC_PATH,
                output_dir=self.output_dir,
            )

    def test_ai_comment_is_lazy_cached_and_token_usage_is_logged(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"}):
            _, first = self.request_json(
                "/api/lab/alerts/comment",
                method="POST",
                payload={"alert": example_alert()},
            )
            _, second = self.request_json(
                "/api/lab/alerts/comment",
                method="POST",
                payload={"alert": example_alert()},
            )

        self.assertTrue(first["available"])
        self.assertFalse(first["cached"])
        self.assertEqual(first["tokenUsage"]["totalTokens"], 58)
        self.assertTrue(second["cached"])
        self.assertEqual(self.ai_calls, 1)
        self.assertTrue((self.output_dir / "ai_cache.json").is_file())
        usage_lines = (self.output_dir / "ai_token_usage.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(len(usage_lines), 1)
        self.assertEqual(json.loads(usage_lines[0])["totalTokens"], 58)

    def test_ai_failure_returns_unavailable_without_exposing_error(self):
        with patch.dict(os.environ, {}, clear=True):
            _, result = self.request_json(
                "/api/lab/alerts/comment",
                method="POST",
                payload={"alert": example_alert()},
            )

        self.assertFalse(result["available"])
        self.assertEqual(result["message"], "AI assist unavailable")
        self.assertEqual(self.ai_calls, 0)


class SmokeReviewWindowTests(unittest.TestCase):
    def test_smoke_window_keeps_engine_alerts_unscored_and_in_memory(self):
        loaded, review_date = smoke_fixture()
        spec = load_spec(SPEC_PATH)

        result = run_smoke_window(
            loaded=loaded,
            spec=spec,
            from_date=review_date,
            to_date=review_date,
        )

        self.assertEqual(len(result["alerts"]), 2)
        self.assertEqual(
            {alert["direction"] for alert in result["alerts"]}, {"BUY", "SELL"}
        )
        for alert in result["alerts"]:
            self.assertFalse(alert["scored"])
            self.assertEqual(alert["period"], "SMOKE")
            self.assertEqual(alert["reward_status"], "NOT_SCORED")
            self.assertEqual(alert["reward_score"], "")
            self.assertIn("candle", alert)
            self.assertIn("previous_candle", alert)
        self.assertEqual(len(result["candles_by_pair"][("XAUUSD", "M5")]), 4)

    def test_cli_smoke_writes_alert_log_but_never_candle_slices(self):
        loaded, review_date = smoke_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            with redirect_stdout(io.StringIO()):
                status = run_smoke_backtest(
                    loaded=loaded,
                    spec=load_spec(SPEC_PATH),
                    from_date=review_date,
                    to_date=review_date,
                    started_at=time.perf_counter(),
                    output_dir=output_dir,
                )

            self.assertEqual(status, 0)
            self.assertEqual(
                {path.name for path in output_dir.iterdir()},
                {"alerts.csv.gz"},
            )
            with gzip.open(output_dir / "alerts.csv.gz", "rt", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["direction"] for row in rows}, {"BUY", "SELL"})

    def test_gemini_request_uses_fixed_prompt_without_network(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "A short context comment."}]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 24,
                    "candidatesTokenCount": 8,
                    "totalTokenCount": 32,
                },
            }
        ).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=response) as mocked_open:
            comment, usage = _gemini_comment(example_alert(), "test-gemini-key")

        request = mocked_open.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            body["systemInstruction"]["parts"][0]["text"], GEMINI_SYSTEM_PROMPT
        )
        self.assertIn("test-gemini-key", request.headers.values())
        self.assertEqual(comment, "A short context comment.")
        self.assertEqual(usage["totalTokens"], 32)


if __name__ == "__main__":
    unittest.main()
