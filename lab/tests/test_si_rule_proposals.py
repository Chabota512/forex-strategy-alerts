from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lab.server import LabService
from lab.si_cases import SICaseStore
from lab.si_rule_proposals import (
    PROPOSAL_CASE_THRESHOLD,
    build_aggregate_summary,
    build_proposal_prompt,
    candidate_duplicates,
    normalize_condition,
    parse_candidate,
)


def _case(
    index: int,
    *,
    alert_id: str | None = None,
    case_type: str = "real",
    net_r: float | None = None,
) -> dict:
    return {
        "case_id": f"case-{index}-{case_type}",
        "alert_id": alert_id or f"alert-{index}",
        "trade_id": f"trade-{index}-{case_type}",
        "case_type": case_type,
        "source": "test",
        "source_id": "test",
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "resolved_at_utc": f"2026-01-{index % 28 + 1:02d}T00:00:00Z",
        "outcome": "WIN" if (net_r if net_r is not None else index % 2) > 0 else "LOSS",
        "net_r": net_r if net_r is not None else (1.0 if index % 2 else -1.0),
        "net_usd": 1.0,
        "features": {
            "direction": "BUY",
            "cct": "uptrend",
            "cst_v2_state": "uptrend",
            "mtf_trend_agree": True,
            "swing_reversal": False,
            "strength_label": "NEUTRAL",
            "tf": "M15",
            "strength_score": 0.5,
            "strength_features": {},
        },
        "formation_fingerprint": {"consecutive_lows_reversal": False},
    }


def _candidate() -> dict:
    return {
        "name": "Observed uptrend sample",
        "condition": {
            "all": [
                {"field": "features.cct", "op": "eq", "value": "uptrend"}
            ]
        },
        "rationale": "The aggregate uptrend cluster is worth further observation.",
    }


def _ready_store(tmp_path: Path, case_count: int = PROPOSAL_CASE_THRESHOLD) -> SICaseStore:
    store = SICaseStore(tmp_path / "cases.jsonl", tmp_path / "beliefs.json")
    store.ingest_cases([_case(index) for index in range(case_count)])
    return store


def test_proposal_gate_and_prompt_use_aggregate_only_data(tmp_path: Path) -> None:
    store = _ready_store(tmp_path, PROPOSAL_CASE_THRESHOLD - 1)
    assert store.proposal_status()["eligible"] is False
    store.ingest_cases([_case(PROPOSAL_CASE_THRESHOLD - 1)])

    status = store.proposal_status()
    assert status["eligible"] is True
    assert status["newResolvedCases"] == PROPOSAL_CASE_THRESHOLD
    attempt = store.begin_proposal_attempt()
    prompt = build_proposal_prompt(attempt["aggregates"])

    assert attempt["aggregates"]["resolvedCaseCount"] == PROPOSAL_CASE_THRESHOLD
    assert "case-0-real" not in prompt
    assert "alert-0" not in prompt
    assert "AGGREGATES" in prompt
    assert "sampleCount" in prompt
    assert store.proposal_status()["generating"] is True


def test_aggregates_include_fade_delta_and_no_case_identifiers() -> None:
    cases = []
    for index in range(30):
        cases.extend(
            [
                _case(index, alert_id=f"paired-{index}", case_type="real", net_r=0.5),
                _case(index, alert_id=f"paired-{index}", case_type="fade", net_r=1.5),
            ]
        )

    aggregate = build_aggregate_summary(cases)
    direction_cluster = next(
        cluster
        for cluster in aggregate["clusters"]
        if cluster["condition"]["all"][0]["field"] == "features.direction"
    )

    assert direction_cluster["sampleCount"] == 60
    assert direction_cluster["fadeVsFollow"] == {
        "pairedAlerts": 30,
        "meanFadeMinusFollowR": 1.0,
    }
    serialized = json.dumps(aggregate)
    assert "paired-0" not in serialized
    assert "case_id" not in serialized
    assert "resolved_at_utc" not in serialized


def test_candidate_validation_rejects_unsafe_and_malformed_conditions() -> None:
    assert parse_candidate(json.dumps(_candidate()))["name"] == _candidate()["name"]
    with pytest.raises(ValueError, match="valid JSON"):
        parse_candidate("{not-json")
    with pytest.raises(ValueError, match="Unsupported condition field"):
        normalize_condition(
            {"all": [{"field": "features.outcome", "op": "eq", "value": "WIN"}]}
        )
    with pytest.raises(ValueError, match="only name, condition, and rationale"):
        parse_candidate(json.dumps({**_candidate(), "cases": []}))


def test_live_builtin_and_lessoned_conditions_are_duplicate_protected() -> None:
    candidate = {
        **_candidate(),
        "condition": {
            "all": [
                {"field": "features.direction", "op": "eq", "value": "BUY"},
                {"field": "features.swing_reversal", "op": "eq", "value": True},
                {
                    "field": "formation_fingerprint.consecutive_lows_reversal",
                    "op": "eq",
                    "value": True,
                },
            ]
        },
    }
    validated = parse_candidate(json.dumps(candidate))
    assert candidate_duplicates(validated, [], []) is True

    rejected = parse_candidate(json.dumps(_candidate()))
    lessons = [
        {
            "evidence": {
                "condition": rejected["condition"],
                "condition_hash": rejected["condition_hash"],
            }
        }
    ]
    assert candidate_duplicates(rejected, [], lessons) is True
    assert candidate_duplicates(rejected, [{"condition": rejected["condition"]}], []) is True


def test_accepted_rule_starts_at_low_confidence_without_historical_backfill(
    tmp_path: Path,
) -> None:
    store = _ready_store(tmp_path)
    context = store.begin_proposal_attempt()
    candidate = parse_candidate(json.dumps(_candidate()))
    store.complete_proposal_attempt(
        context["attempt_id"],
        candidate=candidate,
        usage={"input": 30, "output": 20, "total": 50},
        provider="gemini",
        key_index=1,
        attempts=[{"attempt": 1, "provider": "gemini", "status": "succeeded"}],
    )

    accepted = store.accept_rule_proposal(candidate["id"])
    rule = next(item for item in accepted["rules"] if item["id"] == candidate["id"])
    assert rule["custom"] is True
    assert rule["samples"] == 0
    assert rule["weight"] == 0.2
    assert store.score_alert(
        {
            "direction": "BUY",
            "cct": "uptrend",
            "mtf_trend_agree": True,
            "swing_reversal": False,
        },
        {"consecutive_lows_reversal": False},
    ) == 0.5

    store.ingest_cases([_case(PROPOSAL_CASE_THRESHOLD, net_r=1.0)])
    updated = next(
        item for item in store.snapshot()["rules"] if item["id"] == candidate["id"]
    )
    assert updated["samples"] == 1
    assert updated["hits"] == 1
    assert updated["weight"] == 0.2
    assert store.proposal_status()["tokenUsage"]["total"] == 50


def test_reject_archives_reason_and_prevents_reproposal(tmp_path: Path) -> None:
    store = _ready_store(tmp_path)
    context = store.begin_proposal_attempt()
    candidate = parse_candidate(json.dumps(_candidate()))
    store.complete_proposal_attempt(
        context["attempt_id"],
        candidate=candidate,
        usage={"input": 3, "output": 4, "total": 7},
        provider="gemini",
        key_index=1,
        attempts=[],
    )

    result = store.reject_rule_proposal(candidate["id"], "Insufficient validation evidence.")

    assert result["lessons"][-1]["action"] == "Archived owner-rejected belief rule proposal."
    assert result["lessons"][-1]["evidence"]["reason"] == "Insufficient validation evidence."
    assert candidate_duplicates(candidate, [], store.snapshot()["lessons"]) is True
    assert store.proposal_status()["pending"] is None


def test_service_uses_gemini_and_records_token_usage_with_mocked_provider(
    tmp_path: Path,
) -> None:
    store = _ready_store(tmp_path)
    service = LabService.__new__(LabService)
    service.si_case_store = store
    mock_response = SimpleNamespace(
        diff_json=json.dumps(_candidate()),
        usage={"input": 100, "output": 25, "total": 125},
        provider="gemini",
        key_index=2,
        attempts=[{"attempt": 1, "provider": "gemini", "status": "succeeded"}],
    )
    with patch("lab.server.llm_client.propose", return_value=mock_response) as mocked:
        result = service.propose_system_intelligence_rule()

    assert mocked.call_args.kwargs == {"provider": "gemini"}
    assert "case-0-real" not in mocked.call_args.args[0]
    assert result["pending"]["candidate"]["name"] == _candidate()["name"]
    assert result["latestAttempt"]["token_usage"]["total"] == 125
    assert result["tokenUsage"]["total"] == 125


def test_mocked_gemini_proposal_accepts_learns_and_is_never_reproposed(
    tmp_path: Path,
) -> None:
    store = _ready_store(tmp_path)
    service = LabService.__new__(LabService)
    service.si_case_store = store
    mock_response = SimpleNamespace(
        diff_json=json.dumps(_candidate()),
        usage={"input": 100, "output": 25, "total": 125},
        provider="gemini",
        key_index=2,
        attempts=[{"attempt": 1, "provider": "gemini", "status": "succeeded"}],
    )

    with patch("lab.server.llm_client.propose", return_value=mock_response) as mocked:
        proposed = service.propose_system_intelligence_rule()

    pending = proposed["pending"]
    assert pending["evidence"]["resolvedCaseCount"] == PROPOSAL_CASE_THRESHOLD
    assert pending["evidence"]["clusters"][0]["sampleCount"] == PROPOSAL_CASE_THRESHOLD
    assert pending["evidence"]["clusters"][0]["expectancyR"] is not None
    assert "not a joint rule backtest" in pending["evidence"]["interpretation"]

    accepted = store.accept_rule_proposal(pending["proposal_id"])
    accepted_rule = next(
        rule for rule in accepted["rules"] if rule["id"] == pending["candidate"]["id"]
    )
    assert accepted_rule["weight"] == 0.2
    assert accepted_rule["samples"] == 0

    store.ingest_cases([_case(PROPOSAL_CASE_THRESHOLD, net_r=1.0)])
    learning_rule = next(
        rule
        for rule in store.snapshot()["rules"]
        if rule["id"] == pending["candidate"]["id"]
    )
    assert learning_rule["samples"] == 1
    assert learning_rule["hits"] == 1
    assert learning_rule["hit_rate"] == 1.0

    store.ingest_cases(
        [_case(index, net_r=1.0 if index % 2 else -1.0) for index in range(51, 101)]
    )
    with patch("lab.server.llm_client.propose", return_value=mock_response) as mocked_again:
        duplicate = service.propose_system_intelligence_rule()

    assert mocked.called
    assert mocked_again.called
    assert duplicate["latestAttempt"]["status"] == "discarded"
    assert "duplicates an active" in duplicate["latestAttempt"]["reason"]
    assert duplicate["pending"] is None
    learning_rule = next(
        rule
        for rule in store.snapshot()["rules"]
        if rule["id"] == pending["candidate"]["id"]
    )
    assert learning_rule["samples"] == 51


def test_malformed_model_proposal_is_discarded_with_reason_and_usage(
    tmp_path: Path,
) -> None:
    store = _ready_store(tmp_path)
    service = LabService.__new__(LabService)
    service.si_case_store = store
    mock_response = SimpleNamespace(
        diff_json="{not-json",
        usage={"input": 11, "output": 2, "total": 13},
        provider="gemini",
        key_index=1,
        attempts=[{"attempt": 1, "provider": "gemini", "status": "succeeded"}],
    )
    with patch("lab.server.llm_client.propose", return_value=mock_response):
        result = service.propose_system_intelligence_rule()

    assert result["latestAttempt"]["status"] == "discarded"
    assert "valid JSON" in result["latestAttempt"]["reason"]
    assert result["tokenUsage"]["total"] == 13
    assert result["pending"] is None
