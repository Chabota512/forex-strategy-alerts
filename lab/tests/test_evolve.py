from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lab import evolve
from lab import llm_client
from lab import replay
from lab.alert_policy import POLICY_SETTING_KEYS, load_policy, passthrough_policy


def _seed_spec() -> dict:
    return json.loads(evolve.DEFAULT_SEED.read_text(encoding="utf-8"))


def _write_baseline(directory: Path) -> tuple[Path, Path]:
    seed = directory / "seed.json"
    champion = directory / "champion.json"
    seed.write_bytes(evolve.DEFAULT_SEED.read_bytes())
    champion.write_bytes(evolve.DEFAULT_CHAMPION.read_bytes())
    return seed, champion


def _scores(
    validation_pnl: float = 100.0,
    *,
    trades_per_timeframe: int = 120,
    expectancy: float = 0.25,
    covered_months: int = 8,
    training_pnl: float = 55.75,
) -> dict:
    monthly = list(evolve.YEAR_MONTHS[:covered_months])
    by_timeframe = {
        timeframe: {
            "closed_trade_count": trades_per_timeframe,
            "net_pnl_after_costs": validation_pnl / 2,
            "expectancy_after_costs": expectancy,
            "total_net_r_after_costs": expectancy * trades_per_timeframe,
            "wins": trades_per_timeframe // 2,
            "losses": trades_per_timeframe - trades_per_timeframe // 2,
        }
        for timeframe in evolve.TIMEFRAMES
    }
    return {
        "training": {
            "period": "2025-01 through 2025-09",
            "net_pnl_after_costs": training_pnl,
            "closed_trade_count": trades_per_timeframe * 2,
            "expectancy_after_costs": expectancy,
            "by_timeframe": copy.deepcopy(by_timeframe),
        },
        "validation": {
            "period": "2025-10 through 2025-12",
            "net_pnl_after_costs": validation_pnl,
            "closed_trade_count": trades_per_timeframe * 2,
            "expectancy_after_costs": expectancy,
            "by_timeframe": by_timeframe,
        },
        "annual_signal_month_coverage": {
            "months": monthly,
            "covered_count": covered_months,
            "required_count": evolve.MIN_COVERED_MONTHS,
            "passed": covered_months >= evolve.MIN_COVERED_MONTHS,
        },
    }


def _market_fixture() -> evolve.MarketData:
    return evolve.MarketData(
        source="test-fixture",
        source_sha256="fixture-sha256",
        records_by_timeframe={"M5": [], "M15": []},
    )


def _llm_response(
    diff_json: str,
    usage: dict[str, int | None],
    *,
    provider: str = "mock",
    key_index: int = 1,
) -> evolve.LLMProposalResponse:
    try:
        parsed = json.loads(diff_json)
    except json.JSONDecodeError:
        parsed = {}
    rationale = parsed.get("rationale", "") if isinstance(parsed, dict) else ""
    return evolve.LLMProposalResponse(
        diff_json=diff_json,
        rationale=rationale if isinstance(rationale, str) else "",
        usage=usage,
        provider=provider,
        key_index=key_index,
        attempts=[
            {
                "attempt": 1,
                "provider": provider,
                "key_index": key_index,
                "status": "succeeded",
            }
        ],
    )


class _FakeProviderResponse:
    def __init__(self, payload: dict) -> None:
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


def _annual_simulation_fixture():
    records_by_timeframe: dict[str, list[dict]] = {}
    alerts_by_timeframe: dict[str, dict[int, list[dict]]] = {}
    months_by_timeframe: dict[str, evolve.Counter] = {}
    for timeframe in evolve.TIMEFRAMES:
        interval = 5 if timeframe == "M5" else 15
        records: list[dict] = []
        alerts_by_index: dict[int, list[dict]] = {}
        month_counts = evolve.Counter()
        for month in range(1, 13):
            cycles = 5 if month <= 9 else 40
            for cycle in range(cycles):
                signal_time = datetime(
                    2025, month, 10, tzinfo=timezone.utc
                ) + timedelta(minutes=cycle * 3 * interval)
                entry_time = signal_time + timedelta(minutes=interval)
                exit_time = signal_time + timedelta(minutes=2 * interval)
                signal = {
                    "time": signal_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "close": "100.5",
                }
                entry = {
                    "time": entry_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "open": "100.5",
                    "high": "101",
                    "low": "100",
                    "close": "100.7",
                }
                exit_bar = {
                    "time": exit_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "open": "100.7",
                    "high": "110",
                    "low": "100.6",
                    "close": "100.8",
                }
                alert_index = len(records)
                records.extend([signal, entry, exit_bar])
                alerts_by_index[alert_index] = [
                    {
                        "alert_id": f"{timeframe}-{month}-{cycle}",
                        "symbol": "XAUUSD",
                        "timeframe": timeframe,
                        "direction": "BUY",
                        "alert_type": "BUY-A",
                        "strength_label": "STRONG",
                        "candle": signal,
                        "previous_candle": {
                            "time": "2025-01-01T00:00:00Z",
                            "open": "99",
                            "high": "100",
                            "low": "98",
                            "close": "99.5",
                        },
                    }
                ]
                month_counts[f"2025-{month:02d}"] += 1
        records_by_timeframe[timeframe] = records
        alerts_by_timeframe[timeframe] = alerts_by_index
        months_by_timeframe[timeframe] = month_counts

    def alert_fixture(records, _spec, _options, *, timeframe, source_sha256):
        return (
            copy.deepcopy(alerts_by_timeframe[timeframe]),
            copy.deepcopy(months_by_timeframe[timeframe]),
        )

    data = evolve.MarketData(
        source="synthetic-test-fixture",
        source_sha256="fixture-sha256",
        records_by_timeframe=records_by_timeframe,
        loop_window=evolve.LEGACY_XAUUSD_LOOP_WINDOW,
    )
    return data, alert_fixture


def _round_runner(tmp_path: Path, *, answer: str = "n", baseline_pnl=100.0):
    seed, champion = _write_baseline(tmp_path)
    market_data = _market_fixture()
    scores_seen: list[float] = []

    def evaluator(spec, options, _market_data):
        is_candidate = float(options["r_multiple"]) != 2.0
        pnl = (
            baseline_pnl * 1.2
            if is_candidate and baseline_pnl > 1000
            else 120.0 if is_candidate else baseline_pnl
        )
        scores_seen.append(pnl)
        return _scores(pnl)

    response = _llm_response(
        json.dumps(
            {
                "diff": [{"path": "r_multiple", "value": 2.5}],
                "rationale": "Increase the simulated reward multiple.",
                "expected_effect": "Improve net P&L after costs.",
            }
        ),
        {"input": 140, "output": 32, "total": 172},
    )
    prompts: list[str] = []

    def request(prompt: str) -> evolve.LLMProposalResponse:
        prompts.append(prompt)
        return response

    report = evolve.run_rounds(
        rounds=1,
        champion_path=champion,
        seed_path=seed,
        proposals_dir=tmp_path / "proposals",
        discredited_file=tmp_path / "proposals" / "discredited.json",
        changelog_path=tmp_path / "versions" / "changelog.md",
        versions_dir=tmp_path / "versions",
        market_data=market_data,
        request_fn=request,
        evaluator=evaluator,
        owner_input=lambda _question: answer,
        interactive=True,
    )
    return report, champion, seed, prompts, scores_seen


def _write_cached_champion_result(
    proposals_dir: Path, seed_path: Path, market_data: evolve.MarketData
) -> None:
    scores = _scores()
    if market_data.loop_window is not None:
        scores["training"]["period"] = market_data.loop_window.training_label
        scores["validation"]["period"] = market_data.loop_window.validation_label
    scores["training"]["digest"] = {"snapshot_count": 30}
    proposals_dir.mkdir(parents=True, exist_ok=True)
    (proposals_dir / "evolve_round_00_cached_result.json").write_text(
        json.dumps(
            {
                "baseline_version": str(_seed_spec()["version"]),
                "seed_sha256": evolve.sha256_file(seed_path),
                "market_source": market_data.source,
                "market_source_sha256": market_data.source_sha256,
                "champion_training": scores["training"],
                "champion_validation": scores["validation"],
            }
        ),
        encoding="utf-8",
    )


class EvolutionLoopTests(unittest.TestCase):
    def test_simulation_metrics_identify_the_exit_model_that_generated_them(self) -> None:
        options = evolve.options_for_spec(_seed_spec())
        options["exit_model"] = "candle_geometry"
        metrics = evolve.simulate_period(
            [
                {
                    "time": "2023-01-01T00:00:00Z",
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "close": "100",
                }
            ],
            {},
            months=("2023-01",),
            options=options,
        )

        self.assertEqual(metrics["exit_model"], "candle_geometry")
        aggregate = evolve._aggregate_window({"M5": metrics, "M15": metrics})
        self.assertEqual(aggregate["exit_model"], "candle_geometry")
        self.assertEqual(aggregate["by_timeframe"]["M5"]["exit_model"], "candle_geometry")

    def test_loop_config_defaults_to_three_year_split_and_keeps_legacy_profile(self) -> None:
        default_window = evolve.load_loop_window()
        legacy_window = evolve.load_loop_window(
            profile="xauusd_legacy_2025"
        )

        self.assertEqual(default_window.profile, "three_year")
        self.assertEqual(default_window.training_months[0], "2023-01")
        self.assertEqual(default_window.training_months[-1], "2024-12")
        self.assertEqual(default_window.validation_months[0], "2025-01")
        self.assertEqual(default_window.validation_months[-1], "2025-12")
        self.assertEqual(legacy_window.training_months, evolve.TRAIN_MONTHS)
        self.assertEqual(legacy_window.validation_months, evolve.VALIDATION_MONTHS)

    def test_prompt_uses_only_training_digest_and_marks_history_untrusted(self) -> None:
        spec = _seed_spec()
        options = evolve.options_for_spec(spec)
        history = [
            {
                "reason_code": "promotion_criteria_not_met",
                "validation_verdict": f"verdict-{index}",
                "validation_expectancy_after_costs": -0.07676673828971665,
                "diff": [{"path": "r_multiple", "value": index + 1}],
                "rationale": f"proposal-{index}",
                "raw_response": "validation result 987654321.25",
            }
            for index in range(11)
        ]
        training_digest = {
            "snapshot_count": 45,
            "sample_floor": 30,
            "groups": [
                {"axis": "variant", "value": "A", "n": 45, "avg_r": 1.234}
            ],
        }
        prompt = evolve.build_prompt(
            spec,
            options,
            training_digest,
            history,
        )

        self.assertIn("1.234", prompt)
        self.assertIn("CHAMPION TRAINING DIGEST", prompt)
        self.assertLess(
            prompt.index("RECENTLY REJECTED"),
            prompt.index("CHAMPION TRAINING DIGEST"),
        )
        self.assertNotIn("net_pnl_after_costs", prompt)
        self.assertIn("UNTRUSTED DISCREDITED HISTORY", prompt)
        self.assertNotIn("987654321.25", prompt)
        self.assertNotIn("proposal-10", prompt)
        self.assertIn("verdict-10", prompt)
        self.assertIn("Variant B is always on", prompt)
        self.assertNotIn("- variant_b:", prompt)
        self.assertNotIn("verdict-2", prompt)
        self.assertIn("−0.0768R", prompt)
        self.assertIn("→", prompt)
        self.assertIn(
            "Prefer exploring settings not recently tried; the whitelist is the search space — vary 2–3 related settings per proposal.",
            prompt,
        )
        self.assertNotIn("follow_through", prompt)
        self.assertIn("validation expectancy_after_costs", prompt)
        self.assertIn("cosmetic to expectancy", prompt)
        self.assertIn("exit_model: swept_level_atr or candle_geometry", prompt)
        self.assertIn("r_multiple applies only to swept_level_atr", prompt)
        source = Path(evolve.__file__).read_text(encoding="utf-8")
        self.assertNotIn("score_alert", source)
        self.assertNotIn("follow_through", source)

    def test_discredited_proposal_is_rejected_before_any_simulation(self) -> None:
        archived_diff = [
            {
                "old_value": ["M5", "M15"],
                "path": "timeframes",
                "structural": True,
                "value": ["M15"],
            }
        ]
        proposals = (
            {
                "diff": [
                    {
                        "path": "timeframes",
                        "value": ["M15"],
                        "structural": True,
                    }
                ],
                "rationale": "Repeat the exact earlier proposal.",
                "expected_effect": "Same expected result.",
            },
            {
                "diff": [
                    {
                        "structural": True,
                        "value": ["M15"],
                        "path": "timeframes",
                    }
                ],
                "rationale": "Reworded rationale with no new evidence.",
                "expected_effect": "Rephrased expected effect.",
            },
        )
        for proposal_payload in proposals:
            with self.subTest(rationale=proposal_payload["rationale"]):
                with tempfile.TemporaryDirectory() as temporary:
                    tmp_path = Path(temporary)
                    seed, champion = _write_baseline(tmp_path)
                    market_data = _market_fixture()
                    proposals_dir = tmp_path / "proposals"
                    _write_cached_champion_result(proposals_dir, seed, market_data)
                    discredited_file = proposals_dir / "discredited.json"
                    discredited_file.write_text(
                        json.dumps({"items": [{"diff": archived_diff,
                                               "reason_code": "promotion_criteria_not_met",
                                               "rationale": "Original wording",
                                               "expected_effect": "Original effect"}]}),
                        encoding="utf-8",
                    )
                    simulation_calls: list[dict] = []

                    def evaluator(_spec, options, _market_data):
                        simulation_calls.append(dict(options))
                        return _scores(100.0)

                    response = _llm_response(
                        json.dumps(proposal_payload),
                        {"input": 10, "output": 5, "total": 15},
                    )
                    report = evolve.run_rounds(
                        champion_path=champion,
                        seed_path=seed,
                        proposals_dir=proposals_dir,
                        discredited_file=discredited_file,
                        changelog_path=tmp_path / "versions" / "changelog.md",
                        versions_dir=tmp_path / "versions",
                        market_data=market_data,
                        request_fn=lambda _prompt: response,
                        evaluator=evaluator,
                        interactive=False,
                    )

                    result = report["rounds"][0]
                    archived = json.loads(discredited_file.read_text())["items"]
                    changelog = (tmp_path / "versions" / "changelog.md").read_text()
                    self.assertEqual(result["decision"], "duplicate_proposal")
                    self.assertEqual(result["reason_code"], "duplicate_proposal")
                    self.assertIn("simulation skipped", result["reason"])
                    self.assertIsNone(result["candidate_file"])
                    self.assertEqual(simulation_calls, [])
                    self.assertEqual(archived[-1]["reason_code"], "duplicate_proposal")
                    self.assertTrue(Path(result["result_file"]).is_file())
                    self.assertIn("**duplicate_proposal**", changelog)

    def test_novel_proposal_still_reaches_candidate_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            seed, champion = _write_baseline(tmp_path)
            market_data = _market_fixture()
            proposals_dir = tmp_path / "proposals"
            _write_cached_champion_result(proposals_dir, seed, market_data)
            discredited_file = proposals_dir / "discredited.json"
            discredited_file.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "diff": [
                                    {
                                        "path": "timeframes",
                                        "value": ["M15"],
                                        "structural": True,
                                    }
                                ],
                                "reason_code": "promotion_criteria_not_met",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            simulation_options: list[dict] = []

            def evaluator(_spec, options, _market_data):
                simulation_options.append(dict(options))
                return _scores(100.0, expectancy=0.25)

            response = _llm_response(
                json.dumps(
                    {
                        "diff": [{"path": "r_multiple", "value": 2.5}],
                        "rationale": "Try a novel reward multiple.",
                        "expected_effect": "Test a new setting.",
                    }
                ),
                {"input": 10, "output": 5, "total": 15},
            )
            report = evolve.run_rounds(
                champion_path=champion,
                seed_path=seed,
                proposals_dir=proposals_dir,
                discredited_file=discredited_file,
                changelog_path=tmp_path / "versions" / "changelog.md",
                versions_dir=tmp_path / "versions",
                market_data=market_data,
                request_fn=lambda _prompt: response,
                evaluator=evaluator,
                interactive=False,
            )

            self.assertEqual(len(simulation_options), 2)
            self.assertEqual(simulation_options[0]["r_multiple"], 2.0)
            self.assertEqual(simulation_options[1]["r_multiple"], 2.5)
            self.assertEqual(report["rounds"][0]["decision"], "not_eligible")

    def test_valid_diff_applies_all_thirteen_whitelisted_settings_to_a_copy(self) -> None:
        spec = _seed_spec()
        original = copy.deepcopy(spec)
        options = evolve.options_for_spec(spec)
        diff = [
            {"path": "timeframes", "value": ["M5"], "structural": True},
            {"path": "require_wp_agree", "value": True, "structural": True},
            {"path": "require_mtf_agree", "value": True, "structural": True},
            {"path": "require_engulf", "value": True, "structural": True},
            {"path": "min_strength", "value": "NEUTRAL", "structural": True},
            {"path": "exit_model", "value": "candle_geometry"},
            {"path": "r_multiple", "value": 2.5},
            {"path": "risk_percent", "value": 1.25},
            {"path": "cooldown_candles", "value": 48, "structural": True},
            {"path": "cct.window", "value": 50, "structural": True},
            {"path": "note_body_ratio", "value": 0.9},
            {
                "path": "strength_weights",
                "value": {
                    "cst_alignment": 0.4,
                    "cct_alignment": 0.1,
                    "wp_agree": 0.1,
                    "engulf": 0.1,
                    "mtf_trend_agree": 0.1,
                    "mtf_wp_agree": 0.1,
                    "dual_tf_alert": 0.1,
                },
            },
            {
                "path": "strength_thresholds",
                "value": {"strong_min": 0.75, "neutral_min": 0.3},
            },
        ]

        candidate, candidate_options, changes = evolve.apply_model_diff(
            spec,
            options,
            diff,
            rationale="Test diff.",
            expected_effect="Test behavior.",
        )

        self.assertEqual(len(changes), 13)
        self.assertEqual(spec, original)
        self.assertEqual(candidate["version"], "1.5.1")
        self.assertFalse(candidate["frozen"])
        self.assertEqual(candidate["cct"]["window"], 50)
        self.assertEqual(candidate_options["r_multiple"], 2.5)
        self.assertEqual(candidate_options["exit_model"], "candle_geometry")
        self.assertEqual(candidate_options["cooldown_candles"], 48)
        self.assertEqual(candidate_options["note_body_ratio"], 0.9)
        self.assertEqual(candidate_options["timeframes"], ["M5"])
        self.assertTrue(candidate_options["require_mtf_agree"])
        self.assertEqual(set(candidate_options), set(evolve.ALLOWED_PATHS))
        self.assertNotIn("variant_b", candidate_options)
        self.assertEqual(candidate_options["strength_thresholds"]["strong_min"], 0.75)
        self.assertAlmostEqual(sum(candidate_options["strength_weights"].values()), 1)

    def test_whitelist_rejects_invalid_types_and_bounds(self) -> None:
        spec = _seed_spec()
        options = evolve.options_for_spec(spec)
        invalid_diffs = [
            {"path": "variant_b", "value": 1, "structural": True},
            {"path": "min_strength", "value": "WEAK", "structural": True},
            {"path": "r_multiple", "value": 4.1},
            {"path": "exit_model", "value": "unknown_model"},
            {"path": "exit_model", "value": []},
            {"path": "risk_percent", "value": 0.24},
            {"path": "cooldown_candles", "value": 49, "structural": True},
            {"path": "cct.window", "value": 4, "structural": True},
            {"path": "note_body_ratio", "value": 0.91},
            {
                "path": "strength_weights",
                "value": {
                    "cst_alignment": 0,
                    "cct_alignment": 0,
                    "wp_agree": 0,
                    "engulf": 0,
                    "mtf_trend_agree": 0,
                    "mtf_wp_agree": 0,
                    "dual_tf_alert": 0,
                },
            },
            {
                "path": "strength_thresholds",
                "value": {"strong_min": 0.2, "neutral_min": 0.3},
            },
            {"path": "variant_b", "value": True, "structural": True},
            {"path": "timeframes", "value": []},
            {"path": "timeframes", "value": ["M1"]},
            {"path": "timeframes", "value": ["M5", "M5"]},
            {"path": "timeframes", "value": ("M5",)},
            {"path": "require_mtf_agree", "value": 1, "structural": True},
            {"path": "trigger.sell", "value": False, "structural": True},
        ]
        for change in invalid_diffs:
            with self.subTest(change=change):
                with self.assertRaises(evolve.EvolutionError):
                    evolve.apply_model_diff(
                        spec,
                        options,
                        [change],
                        rationale="Invalid.",
                        expected_effect="Must be rejected.",
                    )

    def test_structural_paths_require_true_marker_and_other_paths_forbid_it(self) -> None:
        spec = _seed_spec()
        options = evolve.options_for_spec(spec)
        invalid = [
            {"path": "timeframes", "value": ["M5"]},
            {"path": "require_mtf_agree", "value": True},
            {"path": "variant_b", "value": True, "structural": True},
            {"path": "cct.window", "value": 16, "structural": False},
            {"path": "r_multiple", "value": 2.5, "structural": True},
            {"path": "exit_model", "value": "candle_geometry", "structural": True},
        ]
        for change in invalid:
            with self.subTest(change=change):
                with self.assertRaises(evolve.EvolutionError):
                    evolve.apply_model_diff(
                        spec,
                        options,
                        [change],
                        rationale="Invalid structural marker.",
                        expected_effect="Must be rejected.",
                    )

    def test_proposal_parser_accepts_no_change_and_rejects_malformed_shapes(self) -> None:
        spec = _seed_spec()
        options = evolve.options_for_spec(spec)
        candidate, candidate_options, proposal = evolve.parse_model_proposal(
            '{"no_change":true}', spec, options
        )
        self.assertIsNone(candidate)
        self.assertIsNone(candidate_options)
        self.assertTrue(proposal["no_change"])
        for text in (
            "{broken",
            '{"no_change":true,"rationale":"extra"}',
            '{"diff":[],"rationale":"x","expected_effect":"y"}',
        ):
            with self.subTest(text=text):
                with self.assertRaises(evolve.EvolutionError):
                    evolve.parse_model_proposal(text, spec, options)

    def test_alert_generation_always_emits_variant_b_and_applies_filters(self) -> None:
        records = [
            {
                "time": f"2025-01-01T00:0{i}:00Z",
                "open": "100",
                "high": "103",
                "low": "98",
                "close": "102" if i == 0 else "101",
            }
            for i in range(4)
        ]
        spec = _seed_spec()
        options = evolve.options_for_spec(spec)
        options.update(
            {
                "require_wp_agree": True,
                "require_engulf": True,
                "min_strength": "STRONG",
                "note_body_ratio": 0.9,
            }
        )
        with (
            patch("lab.evolve.compute_cst", return_value=["undefined"] * 4),
            patch("lab.evolve.detect_trigger", side_effect=["BUY", "SELL", None]),
            patch("lab.evolve.detect_trigger_b", side_effect=["BUY-B", None, None]),
            patch(
                "lab.evolve.calculate_strength",
                return_value=(
                    Decimal("0.9"),
                    "STRONG",
                    {
                        "cst_alignment": 1,
                        "cct_alignment": 1,
                        "wp_agree": 1,
                        "engulf": 1,
                        "mtf_trend_agree": 0,
                        "mtf_wp_agree": 0,
                        "dual_tf_alert": 0,
                    },
                ),
            ),
            patch("lab.evolve.body_engulfs_previous", return_value=True),
            patch("lab.evolve.StreamingWickPressure") as pressure_class,
        ):
            pressure_class.return_value.update.side_effect = ["balanced", "up-pressure", "up-pressure", "up-pressure"]
            alerts, months = evolve.generate_candidate_alerts(
                records,
                spec,
                options,
                timeframe="M5",
                source_sha256="fixture",
            )

        self.assertEqual(
            [item["alert_type"] for item in alerts[1]],
            ["BUY-A", "BUY-B"],
        )
        self.assertEqual(alerts[1][0]["strength_score"], "0.9")
        self.assertNotIn(2, alerts)
        self.assertEqual(months["2025-01"], 2)
        self.assertEqual(alerts[1][0]["note"], "smaller body")

    def test_cooldown_suppresses_signals_for_the_requested_candle_count(self) -> None:
        records = [
            {
                "time": f"2025-01-01T00:0{i}:00Z",
                "open": "100",
                "high": "102",
                "low": "98",
                "close": "101",
            }
            for i in range(5)
        ]
        options = evolve.options_for_spec(_seed_spec())
        options["cooldown_candles"] = 1
        with (
            patch("lab.evolve.compute_cst", return_value=["undefined"] * 5),
            patch("lab.evolve.detect_trigger", side_effect=["BUY", "BUY", "BUY", "BUY"]),
            patch("lab.evolve.detect_trigger_b", return_value=None),
            patch("lab.evolve.strength_for", return_value="STRONG"),
            patch("lab.evolve.StreamingWickPressure") as pressure_class,
        ):
            pressure_class.return_value.update.return_value = "up-pressure"
            alerts, _months = evolve.generate_candidate_alerts(
                records,
                _seed_spec(),
                options,
                timeframe="M5",
                source_sha256="fixture",
            )

        self.assertEqual(sorted(alerts), [1, 3])

    def test_simulation_uses_next_open_r_multiple_and_cost_adjusted_pnl(self) -> None:
        records = [
            {
                "time": "2025-01-01T00:00:00Z",
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
            },
            {
                "time": "2025-01-01T00:05:00Z",
                "open": "100",
                "high": "104",
                "low": "99",
                "close": "103",
            },
        ]
        alert = {
            "alert_id": "test-buy",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "alert_type": "BUY-A",
            "strength_label": "STRONG",
            "candle": records[0],
            "previous_candle": {
                "time": "2024-12-31T23:55:00Z",
                "open": "99",
                "high": "100",
                "low": "98",
                "close": "99",
            },
        }
        options = evolve.options_for_spec(_seed_spec())
        result = evolve.simulate_period(
            records,
            {0: [alert]},
            months=("2025-01",),
            options=options,
        )

        self.assertEqual(result["closed_trade_count"], 1)
        self.assertEqual(result["net_pnl_after_costs"], 182.5)
        self.assertGreater(result["expectancy_after_costs"], 0)

        records_by_timeframe: dict[str, list[dict]] = {}
        for timeframe in evolve.TIMEFRAMES:
            interval = "15" if timeframe == "M15" else "05"
            annual_records: list[dict] = []
            for month in range(1, 13):
                annual_records.extend(
                    [
                        {
                            "time": f"2025-{month:02d}-01T00:00:00Z",
                            "open": "100",
                            "high": "101",
                            "low": "99",
                            "close": "100",
                        },
                        {
                            "time": f"2025-{month:02d}-01T00:{interval}:00Z",
                            "open": "100",
                            "high": "112",
                            "low": "99",
                            "close": "100",
                        },
                    ]
                )
            records_by_timeframe[timeframe] = annual_records

        def fixture_alerts(
            records,
            _spec,
            _options,
            *,
            timeframe,
            source_sha256,
            mtf_context=None,
            symbol="XAUUSD",
        ):
            alerts_by_index = {}
            counts = evolve.Counter()
            for index in range(0, len(records), 2):
                current = records[index]
                alerts_by_index[index] = [
                    {
                        "alert_id": f"{timeframe}-{index}",
                        "time": current["time"],
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "direction": "BUY",
                        "alert_type": "BUY-A",
                        "strength_label": "STRONG",
                        "cct": "undefined",
                        "cst": "undefined",
                        "dc_state": "undefined",
                        "hier_state": "undefined",
                        "wick_pressure": "balanced",
                        "engulfing": False,
                        "candle": current,
                        "previous_candle": {
                            "time": "2024-12-31T23:55:00Z",
                            "open": "99",
                            "high": "100",
                            "low": "98",
                            "close": "99",
                        },
                    }
                ]
                counts[str(current["time"])[:7]] += 1
            return alerts_by_index, counts

        with patch("lab.evolve.generate_candidate_alerts", side_effect=fixture_alerts):
            annual_scores = evolve.evaluate_strategy(
                _seed_spec(),
                options,
                evolve.MarketData(
                    source="test-fixture",
                    source_sha256="fixture",
                    records_by_timeframe=records_by_timeframe,
                    loop_window=evolve.LEGACY_XAUUSD_LOOP_WINDOW,
                ),
            )
        self.assertEqual(
            annual_scores["objective"], "validation expectancy_after_costs"
        )
        self.assertEqual(annual_scores["active_timeframes"], list(evolve.TIMEFRAMES))
        self.assertIn("cosmetic to expectancy", annual_scores["risk_percent_note"])
        self.assertEqual(
            annual_scores["training"]["digest"]["active_timeframes"],
            list(evolve.TIMEFRAMES),
        )
        self.assertEqual(
            annual_scores["validation"]["net_pnl_after_costs"],
            sum(
                annual_scores["validation"]["by_timeframe"][timeframe][
                    "net_pnl_after_costs"
                ]
                for timeframe in evolve.TIMEFRAMES
            ),
        )
        self.assertEqual(
            annual_scores["training"]["closed_trade_count"],
            sum(
                annual_scores["training"]["by_timeframe"][timeframe][
                    "closed_trade_count"
                ]
                for timeframe in evolve.TIMEFRAMES
            ),
        )
        self.assertEqual(
            annual_scores["validation"]["closed_trade_count"],
            sum(
                annual_scores["validation"]["by_timeframe"][timeframe][
                    "closed_trade_count"
                ]
                for timeframe in evolve.TIMEFRAMES
            ),
        )
        self.assertGreater(annual_scores["training"]["digest"]["snapshot_count"], 0)
        self.assertEqual(
            annual_scores["training"]["digest"]["period"],
            "2025-01 through 2025-09",
        )
        self.assertNotIn(
            "snapshots",
            annual_scores["training"]["by_timeframe"]["M5"],
        )
        self.assertEqual(
            annual_scores["annual_signal_month_coverage"]["covered_count"],
            12,
        )

        m5_only_options = {**options, "timeframes": ["M5"]}
        with patch("lab.evolve.generate_candidate_alerts", side_effect=fixture_alerts):
            m5_only_scores = evolve.evaluate_strategy(
                _seed_spec(),
                m5_only_options,
                evolve.MarketData(
                    source="test-fixture",
                    source_sha256="fixture",
                    records_by_timeframe=records_by_timeframe,
                    loop_window=evolve.LEGACY_XAUUSD_LOOP_WINDOW,
                ),
            )
        self.assertEqual(m5_only_scores["active_timeframes"], ["M5"])
        self.assertEqual(
            list(m5_only_scores["validation"]["by_timeframe"]), ["M5"]
        )
        self.assertEqual(
            m5_only_scores["training"]["digest"]["active_timeframes"], ["M5"]
        )

    def test_validation_trade_and_annual_month_floors_are_enforced(self) -> None:
        champion = _scores(100)
        candidate = _scores(150, trades_per_timeframe=99, covered_months=7)
        eligible, reasons, comparison = evolve.assess_promotion(candidate, champion)

        self.assertFalse(eligible)
        self.assertIn("validation_trade_floor_m5", reasons)
        self.assertIn("validation_trade_floor_m15", reasons)
        self.assertIn("annual_signal_month_coverage", reasons)
        self.assertEqual(comparison["annual_signal_month_coverage"]["covered_count"], 7)

    def test_promotion_uses_expectancy_margin_and_positive_gate(self) -> None:
        champion = _scores(100, expectancy=0.25)
        small_gain = _scores(109, expectancy=0.26)
        eligible, reasons, _comparison = evolve.assess_promotion(small_gain, champion)
        self.assertFalse(eligible)
        self.assertIn("validation_expectancy_margin", reasons)

        zero_expectancy = _scores(1000, expectancy=0)
        eligible, reasons, _comparison = evolve.assess_promotion(
            zero_expectancy, champion
        )
        self.assertFalse(eligible)
        self.assertIn("non_positive_validation_expectancy", reasons)

        eligible, reasons, comparison = evolve.assess_promotion(
            _scores(1, expectancy=0.30), champion
        )
        self.assertTrue(eligible)
        self.assertEqual(reasons, [])
        self.assertEqual(comparison["absolute_uplift"], -99)
        self.assertFalse(comparison["pnl_is_deciding"])

    def test_negative_expectancies_do_not_promote_even_with_relative_improvement(self) -> None:
        champion = _scores(100, expectancy=-0.10)
        candidate = _scores(150, expectancy=-0.05)
        eligible, reasons, comparison = evolve.assess_promotion(candidate, champion)

        self.assertFalse(eligible)
        self.assertIn("non_positive_validation_expectancy", reasons)
        self.assertNotIn("validation_expectancy_margin", reasons)
        self.assertEqual(
            comparison["required_validation_expectancy_after_costs"], -0.11
        )

    def test_archived_round_one_is_rejected_for_worsened_expectancy(self) -> None:
        candidate = _scores(-18943.43367543632, expectancy=-0.14290476737424634)
        champion = _scores(-19890.989514002184, expectancy=-0.119861155936757)
        candidate["active_timeframes"] = ["M5", "M15"]
        candidate["validation"]["by_timeframe"] = {
            "M5": {
                "closed_trade_count": 3649,
                "net_pnl_after_costs": -9947.485493806334,
                "expectancy_after_costs": -0.13329625569978293,
                "total_net_r_after_costs": -486.3980370485079,
            },
            "M15": {
                "closed_trade_count": 1277,
                "net_pnl_after_costs": -8995.948181629987,
                "expectancy_after_costs": -0.1703608825661938,
                "total_net_r_after_costs": -217.55084703702948,
            },
        }
        champion["validation"]["by_timeframe"] = {
            "M5": {
                "closed_trade_count": 16355,
                "net_pnl_after_costs": -9999.999999164582,
                "expectancy_after_costs": -0.1338658535701425,
                "total_net_r_after_costs": -2189.3760351396804,
            },
            "M15": {
                "closed_trade_count": 5315,
                "net_pnl_after_costs": -9890.989514837604,
                "expectancy_after_costs": -0.07676673828971665,
                "total_net_r_after_costs": -408.01521400984404,
            },
        }

        eligible, reasons, comparison = evolve.assess_promotion(candidate, champion)

        self.assertFalse(eligible)
        self.assertIn("expectancy_worsened", reasons)
        self.assertIn("non_positive_validation_expectancy", reasons)
        self.assertAlmostEqual(comparison["improvement_percent"], 4.76374409578184)
        self.assertEqual(
            comparison["expectancy_reason"],
            "Validation expectancy worsened: candidate −0.1429R vs champion −0.1199R.",
        )

    def test_validation_trade_floors_apply_only_to_selected_timeframes(self) -> None:
        champion = _scores(100, expectancy=0.25)
        candidate = _scores(100, expectancy=0.30)
        candidate["active_timeframes"] = ["M5"]
        candidate["validation"]["by_timeframe"]["M5"]["closed_trade_count"] = 120
        candidate["validation"]["by_timeframe"]["M15"]["closed_trade_count"] = 0

        eligible, reasons, comparison = evolve.assess_promotion(candidate, champion)

        self.assertTrue(eligible)
        self.assertEqual(reasons, [])
        self.assertEqual(list(comparison["validation_trade_floors"]), ["M5"])
        self.assertEqual(comparison["active_timeframes"], ["M5"])

    def test_mtf_context_is_causal_at_close_and_prefix_invariant(self) -> None:
        def candles(start_minute: int, count: int, interval: int) -> list[dict]:
            base = datetime(2025, 1, 1, tzinfo=timezone.utc)
            result = []
            for index in range(count):
                time_value = base + timedelta(
                    minutes=start_minute + index * interval
                )
                result.append(
                    {
                        "time": time_value.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "open": "100",
                        "high": "102",
                        "low": "98",
                        "close": "101",
                    }
                )
            return result

        m5_records = candles(10, 7, 5)
        m15_records = candles(0, 4, 15)
        m15_alerts = {1: [{"direction": "BUY"}], 2: [{"direction": "SELL"}]}

        class SequencedCst:
            def __init__(self, **_kwargs):
                self.index = 0

            def update(self, _candle):
                states = ["downtrend", "uptrend", "downtrend", "uptrend"]
                state = states[self.index]
                self.index += 1
                return state

        class FixedPressure:
            def update(self, _candle):
                return "up-pressure"

        def build_context(records):
            with (
                patch("lab.replay.StreamingCST", SequencedCst),
                patch("lab.replay.StreamingWickPressure", FixedPressure),
                patch("lab.replay.classify_cct", return_value="downtrend"),
            ):
                return replay.build_mtf_context(
                    records,
                    m15_records,
                    spec=_seed_spec(),
                    m15_alerts_by_index=m15_alerts,
                )

        full_context = build_context(m5_records)
        prefix_context = build_context(m5_records[:4])
        self.assertEqual(full_context[:4], prefix_context)
        self.assertIsNone(full_context[0]["mtf_cst"])
        self.assertEqual(full_context[1]["mtf_cst"], "downtrend")
        self.assertEqual(full_context[4]["mtf_cst"], "uptrend")
        self.assertFalse(full_context[3]["dual_tf_directions"]["BUY"])
        self.assertTrue(full_context[4]["dual_tf_directions"]["BUY"])

    def test_batch_and_stream_mtf_states_match(self) -> None:
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)

        def records(count: int, interval: int) -> list[dict]:
            result = []
            for index in range(count):
                time_value = base + timedelta(minutes=index * interval)
                open_value = Decimal("100") + Decimal(index) / Decimal("10")
                close_value = open_value + (
                    Decimal("0.2") if index % 2 == 0 else Decimal("-0.2")
                )
                result.append(
                    {
                        "time": time_value.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "open": str(open_value),
                        "high": str(max(open_value, close_value) + Decimal("0.3")),
                        "low": str(min(open_value, close_value) - Decimal("0.3")),
                        "close": str(close_value),
                    }
                )
            return result

        m5_records = records(8, 5)
        m15_records = records(5, 15)
        spec = _seed_spec()
        expected = replay.build_mtf_context(
            m5_records, m15_records, spec=spec
        )
        session = replay.ReplaySession(
            session_id="mtf-test",
            run_id="mtf-run",
            timeframe="M5",
            from_value="2025-01-01",
            to_value="2025-01-01",
            records=m5_records,
            source="fixture",
            source_sha256="fixture",
            spec=spec,
            settings_fingerprint="fixture",
            mtf_records=m15_records,
        )
        session.initial_response()
        session.advance(
            to_index=len(m5_records) - 1,
            view_start=0,
            view_end=len(m5_records) - 1,
        )

        self.assertEqual(
            [
                (
                    state["mtfCst"],
                    state["mtfCct"],
                    state["mtfWickPressure"],
                    state["mtfClosedAt"],
                )
                for state in session.states
            ],
            [
                (
                    context["mtf_cst"],
                    context["mtf_cct"],
                    context["mtf_wick_pressure"],
                    context["mtf_closed_at"],
                )
                for context in expected
            ],
        )

    def test_mtf_alert_fields_and_agreement_filter(self) -> None:
        records = [
            {
                "time": "2025-01-01T00:00:00Z",
                "open": "100",
                "high": "103",
                "low": "98",
                "close": "99",
            },
            {
                "time": "2025-01-01T00:05:00Z",
                "open": "99",
                "high": "101",
                "low": "97",
                "close": "100",
            },
        ]
        context = [
            {},
            {
                "mtf_cst": "uptrend",
                "mtf_cct": "uptrend",
                "mtf_wick_pressure": "up-pressure",
                "mtf_closed_at": "2025-01-01T00:00:00Z",
                "dual_tf_directions": {"BUY": True, "SELL": False},
            },
        ]
        spec = _seed_spec()
        options = evolve.options_for_spec(spec)

        def run(require_agreement: bool, mtf_state: str):
            selected_context = [context[0], {**context[1], "mtf_cst": mtf_state}]
            selected_options = {**options, "require_mtf_agree": require_agreement}
            with (
                patch("lab.evolve.compute_cst", return_value=["undefined"] * 2),
                patch("lab.evolve.compute_shadow_states", return_value=[
                    {"dc_state": "undefined", "hier_state": "undefined", "hier_promotions": []},
                    {"dc_state": "undefined", "hier_state": "undefined", "hier_promotions": []},
                ]),
                patch("lab.evolve.detect_trigger", return_value="BUY"),
                patch("lab.evolve.detect_trigger_b", return_value=None),
                patch("lab.evolve.strength_for", return_value="STRONG"),
                patch("lab.evolve.body_engulfs_previous", return_value=False),
                patch("lab.evolve.StreamingWickPressure") as pressure_class,
            ):
                pressure_class.return_value.update.return_value = "balanced"
                return evolve.generate_candidate_alerts(
                    records,
                    spec,
                    selected_options,
                    timeframe="M5",
                    source_sha256="fixture",
                    mtf_context=selected_context,
                )

        alerts, _months = run(False, "uptrend")
        alert = alerts[1][0]
        self.assertTrue(alert["mtf_trend_agree"])
        self.assertTrue(alert["mtf_cct_agree"])
        self.assertTrue(alert["mtf_wp_agree"])
        self.assertTrue(alert["dual_tf_alert"])

        filtered_alerts, _months = run(True, "downtrend")
        self.assertNotIn(1, filtered_alerts)

    def test_mtf_fields_persist_to_snapshots_and_digest_axes(self) -> None:
        alert = {
            "alert_id": "mtf-snapshot",
            "symbol": "XAUUSD",
            "timeframe": "M5",
            "time": "2025-01-01T00:05:00Z",
            "direction": "BUY",
            "alert_type": "BUY-A",
            "strength_label": "STRONG",
            "engulfing": False,
            "candle": {
                "time": "2025-01-01T00:05:00Z",
                "open": "100",
                "high": "103",
                "low": "98",
                "close": "101",
            },
            "previous_candle": {
                "time": "2025-01-01T00:00:00Z",
                "open": "101",
                "high": "102",
                "low": "99",
                "close": "100",
            },
            "mtf_cst": "uptrend",
            "mtf_cct": "uptrend",
            "mtf_wick_pressure": "up-pressure",
            "mtf_closed_at": "2025-01-01T00:00:00Z",
            "mtf_trend_agree": True,
            "mtf_cct_agree": True,
            "mtf_wp_agree": True,
            "dual_tf_alert": True,
        }
        snapshot = evolve.build_alert_snapshot(
            alert, atr14="1", wp_state="up-pressure"
        )
        digest = evolve.build_digest([snapshot], min_cell_size=1)

        self.assertTrue(snapshot["mtf_trend_agree"])
        self.assertTrue(snapshot["dual_tf_alert"])
        self.assertEqual(snapshot["mtf_cst"], "uptrend")
        self.assertTrue(
            any(
                group["axis"] == "mtf_trend_agree" and group["value"] == "True"
                for group in digest["groups"]
            )
        )

    def test_gemini_provider_returns_normalized_response_and_logs_metadata(self) -> None:
        response_body = {
            "candidates": [
                {"content": {"parts": [{"text": '{"no_change":true}'}]}}
            ],
            "usageMetadata": {
                "promptTokenCount": 21,
                "candidatesTokenCount": 7,
                "totalTokenCount": 28,
            },
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps(response_body).encode("utf-8")

        with (
            patch.dict(
                "os.environ",
                {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "unit-test-key"},
            ),
            patch(
                "lab.llm_client.urllib.request.urlopen",
                return_value=FakeResponse(),
            ) as urlopen,
            self.assertLogs("forex_strategy_lab.llm", level="INFO") as output,
        ):
            result = llm_client.propose("unit test prompt")

        self.assertEqual(urlopen.call_count, 1)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("X-goog-api-key"), "unit-test-key")
        self.assertEqual(result.diff_json, '{"no_change":true}')
        self.assertEqual(
            result.usage,
            {"input": 21, "output": 7, "total": 28},
        )
        self.assertEqual(result.provider, "gemini")
        self.assertEqual(result.key_index, 1)
        self.assertTrue(any("key_index=1" in line for line in output.output))

    def test_gemini_failure_is_redacted_and_not_retried(self) -> None:
        secret = "unit-test-secret-do-not-print"
        with (
            patch.dict(
                "os.environ",
                {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": secret},
            ),
            patch(
                "lab.llm_client.urllib.request.urlopen",
                side_effect=RuntimeError(f"upstream echoed {secret}"),
            ) as urlopen,
        ):
            with self.assertRaises(llm_client.LLMRequestError) as caught:
                llm_client.propose("prompt")

        self.assertEqual(urlopen.call_count, 1)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, json.dumps(caught.exception.to_detail()))

    def test_provider_neutral_round_flow_records_both_clients(self) -> None:
        proposal_text = json.dumps({"no_change": True})
        for provider in ("gemini", "groq"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                tmp_path = Path(temp)
                seed, champion = _write_baseline(tmp_path)
                if provider == "gemini":
                    payload = {
                        "candidates": [
                            {"content": {"parts": [{"text": proposal_text}]}}
                        ],
                        "usageMetadata": {
                            "promptTokenCount": 10,
                            "candidatesTokenCount": 3,
                            "totalTokenCount": 13,
                        },
                    }
                else:
                    payload = {
                        "choices": [
                            {"message": {"content": proposal_text}}
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 3,
                            "total_tokens": 13,
                        },
                    }
                environment = {
                    "LLM_PROVIDER": provider,
                    "GEMINI_API_KEY": "mock-gemini-key",
                    "GROQ_API_KEY": "mock-groq-key",
                }
                with (
                    patch.dict("os.environ", environment),
                    patch(
                        "lab.llm_client.urllib.request.urlopen",
                        return_value=_FakeProviderResponse(payload),
                    ) as urlopen,
                ):
                    report = evolve.run_rounds(
                        champion_path=champion,
                        seed_path=seed,
                        proposals_dir=tmp_path / "proposals",
                        discredited_file=tmp_path / "proposals" / "discredited.json",
                        changelog_path=tmp_path / "versions" / "changelog.md",
                        versions_dir=tmp_path / "versions",
                        market_data=_market_fixture(),
                        request_fn=llm_client.propose,
                        evaluator=lambda *_args: _scores(100),
                        interactive=True,
                    )

                result = report["rounds"][0]
                proposal = json.loads(Path(result["proposal_file"]).read_text())
                self.assertEqual(urlopen.call_count, 1)
                self.assertEqual(result["decision"], "no_change")
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["key_index"], 1)
                self.assertEqual(proposal["provider"], provider)
                self.assertEqual(proposal["key_index"], 1)
                self.assertEqual(proposal["token_usage"]["total"], 13)
                self.assertEqual(proposal["raw_response"], proposal_text)


    def test_real_data_loader_requires_owner_approved_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"
            monthly_root = data_root / "XAUUSD"
            (monthly_root / "M1").mkdir(parents=True)
            (monthly_root / "monthly-manifest.json").write_text(
                json.dumps(
                    {
                        "status": "PENDING_OWNER_REVIEW",
                        "owner_approved": False,
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch(
                    "lab.evolve.load_candle_file",
                    side_effect=AssertionError("must use the monthly data gate"),
                ) as legacy_loader,
                patch(
                    "lab.evolve.require_owner_approved_sanity",
                    side_effect=AssertionError("must use the monthly data gate"),
                ) as legacy_approval,
                patch("lab.evolve.load_monthly_timeframe") as monthly_loader,
            ):
                with self.assertRaisesRegex(evolve.EvolutionError, "OWNER_APPROVED"):
                    evolve.load_approved_market_data(data_root)

        legacy_loader.assert_not_called()
        legacy_approval.assert_not_called()
        monthly_loader.assert_not_called()

    def test_mock_round_archives_candidate_and_result_and_owner_decline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            report, _champion, seed, prompts, scores_seen = _round_runner(
                tmp_path,
                answer="n",
                baseline_pnl=987654.25,
            )
            result = report["rounds"][0]
            proposal = json.loads(Path(result["proposal_file"]).read_text())
            archived_result = json.loads(Path(result["result_file"]).read_text())
            candidate = json.loads(Path(result["candidate_file"]).read_text())
            discredited = json.loads(
                (tmp_path / "proposals" / "discredited.json").read_text()
            )["items"]

            self.assertEqual(result["decision"], "not_eligible")
            self.assertEqual(len(scores_seen), 2)
            self.assertEqual(len(prompts), 1)
            self.assertNotIn("987654.25", prompts[0])
            self.assertIn("CHAMPION TRAINING DIGEST", prompts[0])
            self.assertNotIn("55.75", prompts[0])
            self.assertEqual(proposal["token_usage"]["total"], 172)
            self.assertAlmostEqual(
                archived_result["candidate_validation"]["net_pnl_after_costs"],
                987654.25 * 1.2,
            )
            self.assertEqual(candidate["review"]["decision"], "not_eligible")
            self.assertEqual(discredited[0]["reason_code"], "promotion_criteria_not_met")
            self.assertTrue((tmp_path / "versions" / "changelog.md").is_file())
            self.assertEqual(report["seed_sha256_before"], evolve.sha256_file(seed))
            self.assertEqual(report["seed_sha256_before"], report["seed_sha256_after"])

    def test_owner_approval_promotes_only_candidate_and_keeps_seed_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            seed_path, champion_path = _write_baseline(tmp_path)
            market_data = _market_fixture()
            prompts: list[str] = []

            def request(prompt: str) -> evolve.LLMProposalResponse:
                prompts.append(prompt)
                return _llm_response(
                    json.dumps(
                        {
                            "diff": [{"path": "r_multiple", "value": 2.5}],
                            "rationale": "Improve the reward multiple.",
                            "expected_effect": "Improve validation expectancy in R.",
                        }
                    ),
                    {"input": 200, "output": 30, "total": 230},
                )

            def evaluator(_spec, options, _market_data):
                candidate = float(options["r_multiple"]) != 2.0
                return _scores(
                    120.0 if candidate else 100.0,
                    expectancy=0.30 if candidate else 0.25,
                )

            report = evolve.run_rounds(
                champion_path=champion_path,
                seed_path=seed_path,
                proposals_dir=tmp_path / "proposals",
                discredited_file=tmp_path / "proposals" / "discredited.json",
                changelog_path=tmp_path / "versions" / "changelog.md",
                versions_dir=tmp_path / "versions",
                policy_current_path=tmp_path / "policy_current.json",
                policy_history_dir=tmp_path / "policy_history",
                market_data=market_data,
                request_fn=request,
                evaluator=evaluator,
                owner_input=lambda _question: "y",
                interactive=True,
            )
            promoted = json.loads(champion_path.read_text())
            round_result = report["rounds"][0]

            self.assertEqual(len(prompts), 1)
            self.assertTrue(round_result["eligible"])
            self.assertEqual(
                round_result["candidate_validation"]["by_timeframe"]["M5"][
                    "closed_trade_count"
                ],
                120,
            )
            self.assertEqual(round_result["decision"], "promoted")
            self.assertGreater(
                round_result["comparison"]["improvement_percent"],
                10,
            )
            self.assertGreater(
                round_result["candidate_validation"]["expectancy_after_costs"],
                0,
            )
            self.assertEqual(promoted["version"], "1.5.1")
            self.assertEqual(promoted["status"], "frozen_champion")
            self.assertTrue(promoted["frozen"])
            self.assertEqual(
                report["seed_sha256_before"],
                evolve.sha256_file(seed_path),
            )
            archived = list((tmp_path / "versions").glob("*_v1_5_0.json"))
            self.assertEqual(len(archived), 1)
            current_policy = load_policy(tmp_path / "policy_current.json")
            candidate_options = evolve.options_for_spec(promoted)
            self.assertEqual(current_policy["mode"], "active")
            self.assertEqual(current_policy["version"], 2)
            self.assertEqual(
                current_policy["source_champion_version"], promoted["version"]
            )
            self.assertEqual(
                current_policy["settings"],
                {key: candidate_options[key] for key in POLICY_SETTING_KEYS},
            )
            policy_archives = list((tmp_path / "policy_history").glob("*.json"))
            self.assertEqual(len(policy_archives), 1)
            self.assertEqual(
                json.loads(policy_archives[0].read_text()),
                passthrough_policy(),
            )

    def test_malformed_proposal_is_archived_discredited_and_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            seed, champion = _write_baseline(tmp_path)
            calls: list[str] = []

            def request(prompt):
                calls.append(prompt)
                return _llm_response(
                    "{malformed",
                    {"input": 1, "output": 2, "total": 3},
                )

            report = evolve.run_rounds(
                champion_path=champion,
                seed_path=seed,
                proposals_dir=tmp_path / "proposals",
                discredited_file=tmp_path / "proposals" / "discredited.json",
                changelog_path=tmp_path / "versions" / "changelog.md",
                versions_dir=tmp_path / "versions",
                market_data=_market_fixture(),
                request_fn=request,
                evaluator=lambda *_args: _scores(100),
                interactive=True,
            )
            result = report["rounds"][0]
            history = evolve._load_discredited(
                tmp_path / "proposals" / "discredited.json"
            )
            next_prompt = evolve.build_prompt(
                _seed_spec(),
                evolve.options_for_spec(_seed_spec()),
                _scores()["training"].get("digest", {}),
                history,
            )

            self.assertEqual(len(calls), 1)
            self.assertEqual(result["decision"], "malformed_proposal")
            self.assertTrue(Path(result["proposal_file"]).is_file())
            self.assertTrue(Path(result["result_file"]).is_file())
            self.assertEqual(history[0]["reason_code"], "malformed_json_diff")
            self.assertIn("Malformed JSON diff; proposal discarded.", next_prompt)

    def test_malformed_provider_answer_does_not_fallback(self) -> None:
        answers = (
            "{malformed",
            json.dumps(
                {
                    "diff": [{"path": "not_allowlisted", "value": "secret"}],
                    "rationale": "Reject the unsupported path.",
                    "expected_effect": "No proposal should be accepted.",
                }
            ),
        )
        for response_text in answers:
            with self.subTest(response_text=response_text), tempfile.TemporaryDirectory() as temporary:
                tmp_path = Path(temporary)
                seed, champion = _write_baseline(tmp_path)
                payload = {
                    "candidates": [
                        {"content": {"parts": [{"text": response_text}]}}
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 2,
                        "candidatesTokenCount": 1,
                        "totalTokenCount": 3,
                    },
                }
                with (
                    patch.dict(
                        "os.environ",
                        {
                            "LLM_PROVIDER": "auto",
                            "GEMINI_API_KEY": "mock-gemini-primary",
                            "GEMINI_API_KEY_2": "mock-gemini-secondary",
                            "GROQ_API_KEY": "mock-groq",
                        },
                    ),
                    patch(
                        "lab.llm_client.urllib.request.urlopen",
                        return_value=_FakeProviderResponse(payload),
                    ) as urlopen,
                    patch("lab.llm_client.time.sleep") as sleep,
                ):
                    report = evolve.run_rounds(
                        champion_path=champion,
                        seed_path=seed,
                        proposals_dir=tmp_path / "proposals",
                        discredited_file=tmp_path / "proposals" / "discredited.json",
                        changelog_path=tmp_path / "versions" / "changelog.md",
                        versions_dir=tmp_path / "versions",
                        market_data=_market_fixture(),
                        request_fn=llm_client.propose,
                        evaluator=lambda *_args: _scores(100),
                        interactive=True,
                    )

                result = report["rounds"][0]
                proposal = json.loads(Path(result["proposal_file"]).read_text())
                history = evolve._load_discredited(
                    tmp_path / "proposals" / "discredited.json"
                )
                self.assertEqual(urlopen.call_count, 1)
                sleep.assert_not_called()
                self.assertEqual(result["decision"], "malformed_proposal")
                self.assertEqual(result["provider"], "gemini")
                self.assertEqual(result["key_index"], 1)
                self.assertEqual(
                    proposal["provider_attempts"][0]["status"],
                    "succeeded",
                )
                self.assertEqual(history[0]["reason_code"], "malformed_json_diff")

    def test_request_error_detail_is_archived_with_sanitized_attempts(self) -> None:
        secret = "mock-secret-never-archive"
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            seed, champion = _write_baseline(tmp_path)
            failures = [
                _provider_http_error(429, f"quota exceeded: {secret}"),
                _provider_http_error(429, "quota exceeded"),
                _provider_http_error(503, f"temporary network failure: {secret}"),
            ]
            with (
                patch.dict(
                    "os.environ",
                    {
                        "LLM_PROVIDER": "auto",
                        "GEMINI_API_KEY": "mock-gemini-primary",
                        "GEMINI_API_KEY_2": "mock-gemini-secondary",
                        "GROQ_API_KEY": secret,
                    },
                ),
                patch(
                    "lab.llm_client.urllib.request.urlopen",
                    side_effect=failures,
                ) as urlopen,
                patch("lab.llm_client.time.sleep") as sleep,
            ):
                report = evolve.run_rounds(
                    champion_path=champion,
                    seed_path=seed,
                    proposals_dir=tmp_path / "proposals",
                    discredited_file=tmp_path / "proposals" / "discredited.json",
                    changelog_path=tmp_path / "versions" / "changelog.md",
                    versions_dir=tmp_path / "versions",
                    market_data=_market_fixture(),
                    request_fn=llm_client.propose,
                    evaluator=lambda *_args: _scores(100),
                    interactive=True,
                )

            result = report["rounds"][0]
            proposal = json.loads(Path(result["proposal_file"]).read_text())
            archived_result = json.loads(Path(result["result_file"]).read_text())
            detail = result["request_error_detail"]
            self.assertEqual(urlopen.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 5])
            self.assertEqual(result["decision"], "request_error")
            self.assertEqual(detail["category"], "network")
            self.assertEqual(
                [(item["provider"], item["key_index"], item["category"])
                 for item in detail["attempts"]],
                [
                    ("gemini", 1, "quota"),
                    ("gemini", 2, "quota"),
                    ("groq", 1, "network"),
                ],
            )
            self.assertEqual(proposal["request_error_detail"], detail)
            self.assertEqual(archived_result["request_error_detail"], detail)
            self.assertNotIn(secret, json.dumps(proposal))
            self.assertNotIn(secret, json.dumps(archived_result))


class EvolutionUiGateTests(unittest.TestCase):
    def _run_candidate(
        self,
        tmp_path: Path,
        candidate_scores: dict,
        *,
        owner_decision: bool = True,
        response_text: str | None = None,
    ):
        seed, champion = _write_baseline(tmp_path)
        proposal_text = response_text or json.dumps(
            {
                "diff": [{"path": "r_multiple", "value": 2.5}],
                "rationale": "Increase the simulated reward multiple.",
                "expected_effect": "Improve validation expectancy.",
            }
        )
        requests: list[str] = []
        owner_payloads: list[dict] = []
        phases: list[str] = []

        def request(prompt: str) -> evolve.LLMProposalResponse:
            requests.append(prompt)
            return _llm_response(
                proposal_text,
                {"input": 17, "output": 4, "total": 21},
            )

        def evaluator(_spec, options, _market_data):
            return (
                candidate_scores
                if float(options["r_multiple"]) != 2.0
                else _scores(100, expectancy=0.25)
            )

        def owner_gate(result):
            owner_payloads.append(copy.deepcopy(dict(result)))
            return owner_decision

        report = evolve.run_rounds(
            rounds=1,
            champion_path=champion,
            seed_path=seed,
            proposals_dir=tmp_path / "proposals",
            discredited_file=tmp_path / "proposals" / "discredited.json",
            changelog_path=tmp_path / "versions" / "changelog.md",
            versions_dir=tmp_path / "versions",
            market_data=_market_fixture(),
            request_fn=request,
            evaluator=evaluator,
            owner_gate=owner_gate,
            phase_callback=lambda phase, _message: phases.append(phase),
            interactive=False,
        )
        return report, champion, seed, requests, owner_payloads, phases

    def test_ui_gate_promotes_with_the_same_eligible_candidate_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            report, champion, seed, requests, owner_payloads, phases = (
                self._run_candidate(
                    tmp_path, _scores(120, expectancy=0.30), owner_decision=True
                )
            )
            result = report["rounds"][0]

            self.assertEqual(len(requests), 1)
            self.assertEqual(len(owner_payloads), 1)
            self.assertTrue(owner_payloads[0]["eligible"])
            self.assertEqual(result["decision"], "promoted")
            self.assertEqual(result["token_usage"]["total"], 21)
            self.assertEqual(json.loads(champion.read_text())["version"], "1.5.1")
            self.assertEqual(
                report["seed_sha256_before"], evolve.sha256_file(seed)
            )
            self.assertEqual(report["seed_sha256_before"], report["seed_sha256_after"])
            self.assertEqual(
                phases,
                [
                    "training",
                    "awaiting-proposal",
                    "simulating",
                    "awaiting-owner-decision",
                ],
            )

    def test_ui_gate_decline_does_not_write_policy_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            report, _champion, _seed, _requests, _owner_payloads, _phases = (
                self._run_candidate(
                    tmp_path,
                    _scores(120, expectancy=0.30),
                    owner_decision=False,
                )
            )

            self.assertEqual(report["rounds"][0]["decision"], "rejected")
            self.assertFalse((tmp_path / "policy_current.json").exists())
            self.assertFalse((tmp_path / "policy_history").exists())

    def test_ui_gate_cannot_skip_trade_margin_or_positive_expectancy_rules(self) -> None:
        cases = (
            (
                "trade floor",
                _scores(150, trades_per_timeframe=99, expectancy=0.30),
                "validation_trade_floor_m5",
            ),
            (
                "expectancy margin",
                _scores(150, expectancy=0.26),
                "validation_expectancy_margin",
            ),
            (
                "positive expectancy",
                _scores(150, expectancy=0.0),
                "non_positive_validation_expectancy",
            ),
        )
        for name, scores, expected_reason in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                report, _champion, _seed, requests, owner_payloads, _phases = (
                    self._run_candidate(Path(temporary), scores)
                )
                result = report["rounds"][0]
                self.assertEqual(len(requests), 1)
                self.assertEqual(owner_payloads, [])
                self.assertFalse(result["eligible"])
                self.assertEqual(result["decision"], "not_eligible")
                self.assertIn(expected_reason, result["reason_codes"])

    def test_ui_gate_rejects_malformed_and_out_of_whitelist_proposals(self) -> None:
        bad_responses = (
            "{malformed",
            json.dumps(
                {
                    "diff": [{"path": "unlisted.setting", "value": 1}],
                    "rationale": "Try an unlisted change.",
                    "expected_effect": "Not allowed.",
                }
            ),
        )
        for bad_response in bad_responses:
            with tempfile.TemporaryDirectory() as temporary:
                report, _champion, _seed, requests, owner_payloads, _phases = (
                    self._run_candidate(
                        Path(temporary),
                        _scores(120, expectancy=0.30),
                        response_text=bad_response,
                    )
                )
                self.assertEqual(len(requests), 1)
                self.assertEqual(owner_payloads, [])
                self.assertEqual(
                    report["rounds"][0]["decision"], "malformed_proposal"
                )


if __name__ == "__main__":
    unittest.main()
