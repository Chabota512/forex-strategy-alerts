from __future__ import annotations

import json
import io
import http.client
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import call, patch

from lab import evolve, llm_client
from lab.evolve_jobs import EvolveJobError, EvolveJobManager
from lab.server import make_handler


class _FakeProviderResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self._body


def _provider_http_error(status: int, message: str) -> urllib.error.HTTPError:
    body = json.dumps({"error": {"message": message}}).encode("utf-8")
    return urllib.error.HTTPError(
        "https://provider.invalid/mock",
        status,
        "mock provider failure",
        {},
        io.BytesIO(body),
    )


class EvolveJobApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history_directory = tempfile.TemporaryDirectory(
            prefix="evolve-history-test-"
        )
        self.history_dir = Path(self.history_directory.name)
        self.training_started = threading.Event()
        self.resume_training = threading.Event()
        self.owner_gate_entered = threading.Event()
        self.runner_calls = 0

        def fake_run_rounds(**kwargs: Any) -> dict[str, Any]:
            self.runner_calls += 1
            phase_callback = kwargs["phase_callback"]
            owner_gate = kwargs["owner_gate"]
            phase_callback("training", "Mock training phase.")
            self.training_started.set()
            self.resume_training.wait(timeout=5)
            phase_callback("awaiting-proposal", "Mock one proposal attempt.")
            phase_callback("simulating", "Mock candidate evaluation.")
            partial_result = {
                "round": 1,
                "decision": "pending",
                "eligible": True,
                "reason_codes": [],
                "proposal": {
                    "no_change": False,
                    "diff": [{"path": "r_multiple", "value": 2.5}],
                    "rationale": "Fixture rationale.",
                    "expected_effect": "Fixture effect.",
                },
                "comparison": {"expectancy_margin_passed": True},
                "token_usage": {"input": 17, "output": 4, "total": 21},
                "proposal_file": "lab/specs/proposals/mock_proposal.json",
                "candidate_file": "lab/specs/proposals/mock_candidate.json",
            }
            phase_callback(
                "awaiting-owner-decision", "Mock candidate awaits the owner."
            )
            self.owner_gate_entered.set()
            promote = owner_gate(partial_result)
            result = {
                **partial_result,
                "decision": "promoted" if promote else "rejected",
                "reason": "Mock owner decision.",
                "result_file": "lab/specs/proposals/mock_result.json",
            }
            return {"rounds": [result]}

        self.manager = EvolveJobManager(run_rounds_fn=fake_run_rounds)
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(
                SimpleNamespace(
                    evolve_jobs=self.manager,
                    evolve_proposals_dir=self.history_dir,
                )
            ),
        )
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.resume_training.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.history_directory.cleanup()

    def request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        try:
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def wait_for_phase(self, job_id: str, phase: str) -> dict[str, Any]:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            status, payload = self.request_json(
                f"/api/evolve/status/{job_id}"
            )
            self.assertEqual(status, 200)
            if payload["phase"] == phase and (
                phase != "awaiting-owner-decision" or payload["result"] is not None
            ):
                return payload
            threading.Event().wait(0.01)
        self.fail(f"Evolution job did not reach phase {phase}.")

    def _real_round_runner(self, directory: Path):
        seed = directory / "seed.json"
        champion = directory / "champion.json"
        shutil.copy2(evolve.DEFAULT_SEED, seed)
        shutil.copy2(evolve.DEFAULT_CHAMPION, champion)
        proposals = directory / "proposals"
        market_data = evolve.MarketData(
            source="ui-job-test",
            source_sha256="ui-job-fixture",
            records_by_timeframe={"M5": [], "M15": []},
        )

        def run_with_test_fixtures(**options: Any) -> dict[str, Any]:
            return evolve.run_rounds(
                **options,
                champion_path=champion,
                seed_path=seed,
                proposals_dir=proposals,
                discredited_file=proposals / "discredited.json",
                changelog_path=directory / "versions" / "changelog.md",
                versions_dir=directory / "versions",
                market_data=market_data,
                evaluator=lambda *_args: {
                    "training": {"digest": {}},
                    "validation": {},
                },
            )

        return run_with_test_fixtures

    def test_history_reads_archives_newest_first_with_summary_and_detail(self) -> None:
        older = {
            "round": 1,
            "timestamp_utc": "2026-10-28T20:30:05.389032Z",
            "decision": "not_eligible",
            "eligible": False,
            "proposal": {"no_change": True, "diff": []},
            "candidate_validation": {"expectancy_after_costs": -0.14},
            "token_usage": {"input": 41, "output": 9, "total": 50},
        }
        newest = {
            "round": 3,
            "timestamp_utc": "2026-10-29T02:06:19.859626Z",
            "decision": "not_eligible",
            "eligible": False,
            "proposal": {
                "diff": [
                    {
                        "path": "timeframes",
                        "old_value": ["M5", "M15"],
                        "value": ["M15"],
                    }
                ]
            },
            "candidate_validation": {"expectancy_after_costs": -0.0767},
            "provider_attempts": [
                {"provider": "gemini", "key_index": 1, "status": "failed"},
                {"provider": "gemini", "key_index": 2, "status": "succeeded"},
            ],
            "token_usage": {"input": 4866, "output": 227, "total": 8959},
        }
        (self.history_dir / "evolve_round_01_20261028T202739460309Z_result.json").write_text(
            json.dumps(older), encoding="utf-8"
        )
        (self.history_dir / "evolve_round_03_20261029T020351541650Z_result.json").write_text(
            json.dumps(newest), encoding="utf-8"
        )
        (self.history_dir / "candidate.json").write_text("{}", encoding="utf-8")

        status, payload = self.request_json("/api/evolve/history")

        self.assertEqual(status, 200)
        self.assertEqual(
            [item["round"] for item in payload["rounds"]],
            [3, 1],
        )
        newest_record = payload["rounds"][0]
        self.assertEqual(newest_record["timestamp_utc"], newest["timestamp_utc"])
        self.assertEqual(
            newest_record["diff_summary"],
            'timeframes: ["M5","M15"] → ["M15"]',
        )
        self.assertEqual(newest_record["validation_expectancy"], -0.0767)
        self.assertEqual(newest_record["provider"], "gemini")
        self.assertEqual(newest_record["key_index"], 2)
        self.assertEqual(newest_record["token_usage"], newest["token_usage"])
        self.assertEqual(newest_record["detail"], newest)
        self.assertIsNone(payload["rounds"][1]["provider"])
        self.assertIsNone(payload["rounds"][1]["key_index"])

    def test_job_runs_polls_waits_for_owner_and_surfaces_tokens(self) -> None:
        status, started = self.request_json(
            "/api/evolve/run", method="POST", payload={}
        )
        self.assertEqual(status, 202)
        job_id = started["job_id"]
        self.assertEqual(started["phase"], "training")
        self.assertTrue(self.training_started.wait(timeout=2))

        early_status, early_response = self.request_json(
            "/api/evolve/decide",
            method="POST",
            payload={"job_id": job_id, "promote": True},
        )
        self.assertEqual(early_status, 409)
        self.assertIn("not awaiting", early_response["error"])

        second_status, second_job = self.request_json(
            "/api/evolve/run", method="POST", payload={}
        )
        self.assertEqual(second_status, 409)
        self.assertIn("already running", second_job["error"])

        self.resume_training.set()
        self.assertTrue(self.owner_gate_entered.wait(timeout=2))
        awaiting = self.wait_for_phase(job_id, "awaiting-owner-decision")
        self.assertEqual(
            awaiting["result"]["token_usage"],
            {"input": 17, "output": 4, "total": 21},
        )
        self.assertEqual(self.runner_calls, 1)

        decision_status, decision = self.request_json(
            "/api/evolve/decide",
            method="POST",
            payload={"job_id": job_id, "promote": True},
        )
        self.assertEqual(decision_status, 202)
        self.assertIn(decision["phase"], {"simulating", "complete"})

        completed = self.wait_for_phase(job_id, "complete")
        self.assertEqual(completed["result"]["decision"], "promoted")
        self.assertEqual(
            completed["result"]["token_usage"],
            {"input": 17, "output": 4, "total": 21},
        )
        self.assertTrue(completed["result"]["result_file"].endswith("mock_result.json"))
        self.assertEqual(
            {line["phase"] for line in completed["logs"]},
            {
                "training",
                "awaiting-proposal",
                "simulating",
                "awaiting-owner-decision",
                "complete",
            },
        )

    def test_unknown_job_and_unawaited_decision_are_rejected(self) -> None:
        status, payload = self.request_json(
            "/api/evolve/status/00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(status, 404)
        self.assertIn("not found", payload["error"])

        status, payload = self.request_json(
            "/api/evolve/decide",
            method="POST",
            payload={"job_id": "00000000-0000-0000-0000-000000000000", "promote": False},
        )
        self.assertEqual(status, 404)
        self.assertIn("not found", payload["error"])

    def test_decision_requires_an_eligible_pending_result(self) -> None:
        job_id = self.manager.start()["job_id"]
        self.assertTrue(self.training_started.wait(timeout=2))
        with self.assertRaises(EvolveJobError) as error:
            self.manager.decide(job_id, False)
        self.assertEqual(error.exception.status, 409)
        self.resume_training.set()
        self.assertTrue(self.owner_gate_entered.wait(timeout=2))
        waiting = self.wait_for_phase(job_id, "awaiting-owner-decision")
        self.assertTrue(waiting["result"]["eligible"])
        self.manager.decide(job_id, False)
        completed = self.wait_for_phase(job_id, "complete")
        self.assertEqual(completed["result"]["decision"], "rejected")

    def test_ui_job_uses_fallback_chain_and_archives_attempts(self) -> None:
        gemini_payload = {
            "candidates": [{"content": {"parts": [{"text": '{"no_change":true}'}]}}],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 3,
                "totalTokenCount": 13,
            },
        }
        groq_payload = {
            "choices": [{"message": {"content": '{"no_change":true}'}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "total_tokens": 13,
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            self.manager._run_rounds = self._real_round_runner(Path(temporary))
            with (
                patch.dict(
                    os.environ,
                    {
                        "LLM_PROVIDER": "auto",
                        "GEMINI_API_KEY": "mock-gemini-primary",
                        "GEMINI_API_KEY_2": "mock-gemini-secondary",
                        "GROQ_API_KEY": "mock-groq",
                    },
                    clear=True,
                ),
                patch(
                    "lab.llm_client.urllib.request.urlopen",
                    side_effect=[
                        _provider_http_error(429, "quota exceeded"),
                        _provider_http_error(429, "quota exceeded"),
                        _FakeProviderResponse(groq_payload),
                    ],
                ) as urlopen,
                patch("lab.llm_client.time.sleep") as sleep,
            ):
                response_status, started = self.request_json(
                    "/api/evolve/run", method="POST", payload={}
                )
                self.assertEqual(response_status, 202)
                completed = self.wait_for_phase(started["job_id"], "complete")

            self.assertEqual(completed["result"]["decision"], "no_change")
            self.assertEqual(urlopen.call_count, 3)
            self.assertEqual(
                [
                    (
                        attempt["provider"],
                        attempt["key_index"],
                        attempt.get("category"),
                        attempt["status"],
                    )
                    for attempt in completed["result"]["provider_attempts"]
                ],
                [
                    ("gemini", 1, "quota", "failed"),
                    ("gemini", 2, "quota", "failed"),
                    ("groq", 1, None, "succeeded"),
                ],
            )
            self.assertEqual(
                urlopen.call_args_list[0].args[0].get_header("X-goog-api-key"),
                "mock-gemini-primary",
            )
            self.assertEqual(
                urlopen.call_args_list[1].args[0].get_header("X-goog-api-key"),
                "mock-gemini-secondary",
            )
            self.assertEqual(
                urlopen.call_args_list[2].args[0].full_url,
                llm_client.GROQ_CHAT_COMPLETIONS_URL,
            )
            self.assertEqual(sleep.call_args_list, [call(2), call(5)])

            proposal_archive = json.loads(
                Path(completed["result"]["proposal_file"]).read_text(encoding="utf-8")
            )
            result_archive = json.loads(
                Path(completed["result"]["result_file"]).read_text(encoding="utf-8")
            )
            expected_attempts = completed["result"]["provider_attempts"]
            self.assertEqual(proposal_archive["provider_attempts"], expected_attempts)
            self.assertEqual(result_archive["provider_attempts"], expected_attempts)

    def test_ui_job_does_not_fallback_after_malformed_model_output(self) -> None:
        malformed_payload = {
            "candidates": [{"content": {"parts": [{"text": "{malformed"}]}}],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 3,
                "totalTokenCount": 13,
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            self.manager._run_rounds = self._real_round_runner(Path(temporary))
            with (
                patch.dict(
                    os.environ,
                    {
                        "LLM_PROVIDER": "auto",
                        "GEMINI_API_KEY": "mock-gemini-primary",
                        "GEMINI_API_KEY_2": "mock-gemini-secondary",
                        "GROQ_API_KEY": "mock-groq",
                    },
                    clear=True,
                ),
                patch(
                    "lab.llm_client.urllib.request.urlopen",
                    return_value=_FakeProviderResponse(malformed_payload),
                ) as urlopen,
                patch("lab.llm_client.time.sleep") as sleep,
            ):
                response_status, started = self.request_json(
                    "/api/evolve/run", method="POST", payload={}
                )
                self.assertEqual(response_status, 202)
                completed = self.wait_for_phase(started["job_id"], "complete")

            self.assertEqual(
                completed["result"]["decision"], "malformed_proposal"
            )
            self.assertEqual(urlopen.call_count, 1)
            sleep.assert_not_called()
            proposal_archive = json.loads(
                Path(completed["result"]["proposal_file"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                proposal_archive["provider_attempts"],
                [
                    {
                        "attempt": 1,
                        "provider": "gemini",
                        "key_index": 1,
                        "status": "succeeded",
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
