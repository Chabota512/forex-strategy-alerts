from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from lab.alert_policy import passthrough_policy
from lab.server import LabService
from lab.si_threshold import calibrate_min_si_score


def _scored_case(
    index: int,
    score: float,
    net_r: float,
    *,
    case_type: str = "real",
    symbol: str = "XAUUSD",
) -> dict:
    resolved = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    return {
        "case_id": f"case-{index}-{case_type}-{symbol}",
        "symbol": symbol,
        "case_type": case_type,
        "outcome": "WIN" if net_r > 0 else "LOSS",
        "net_r": net_r,
        "resolved_at_utc": resolved.isoformat().replace("+00:00", "Z"),
        "features": {"si_score": score},
    }


def _fixture_cases() -> list[dict]:
    return [
        *(_scored_case(index, 0.4, -1.0) for index in range(100)),
        *(_scored_case(index + 100, 0.7, 1.0) for index in range(110)),
    ]


def test_auto_si_min_selects_fixture_optimum_with_retention_evidence() -> None:
    cases = _fixture_cases()
    cases.extend(
        [
            _scored_case(211, 1.0, 5.0, case_type="fade"),
            _scored_case(212, 1.0, 5.0, symbol="EURUSD"),
            {
                **_scored_case(213, 1.0, 5.0),
                "features": {},
            },
        ]
    )

    evidence = calibrate_min_si_score(cases)

    assert evidence["status"] == "calibrated"
    assert evidence["threshold"] == 0.41
    assert evidence["caseCount"] == 210
    assert evidence["filteredCaseCount"] == 110
    assert evidence["retention"] == 110 / 210
    assert evidence["retention"] >= 0.4
    assert evidence["baselineExpectancyR"] == 10 / 210
    assert evidence["filteredExpectancyR"] == 1.0


def test_auto_si_min_waits_until_history_is_above_200_cases() -> None:
    evidence = calibrate_min_si_score(_fixture_cases()[:200])

    assert evidence["status"] == "calibrating"
    assert evidence["threshold"] == 0.0
    assert evidence["caseCount"] == 200
    assert evidence["casesNeeded"] == 1
    assert evidence["message"] == "Calibrating after 1 more cases"


def test_auto_si_min_uses_only_the_latest_500_scored_cases() -> None:
    cases = [
        *(_scored_case(index, 0.3, -1.0) for index in range(10)),
        *(_scored_case(index + 10, 0.7, 1.0) for index in range(500)),
    ]

    evidence = calibrate_min_si_score(cases)

    assert evidence["totalScoredCases"] == 510
    assert evidence["caseCount"] == 500
    assert evidence["threshold"] == 0.0
    assert evidence["filteredCaseCount"] == 500
    assert evidence["filteredExpectancyR"] == 1.0


def test_current_policy_exposes_auto_evidence_and_owner_reset() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        policy_path = root / "policy_current.json"
        policy_path.write_text(json.dumps(passthrough_policy()), encoding="utf-8")
        service = LabService.__new__(LabService)
        service.policy_path = policy_path
        service.policy_versions_dir = root / "versions"
        service.si_case_store = SimpleNamespace(scored_case_history=_fixture_cases)

        automatic = service.current_policy()
        assert automatic["settings"]["min_si_score"] == 0.41
        assert automatic["settings"]["min_si_score_mode"] == "auto"
        assert automatic["min_si_score_auto"] == 0.41
        assert automatic["min_si_score_evidence"]["filteredCaseCount"] == 110

        overridden = service.set_min_si_score(0.6)
        assert overridden["settings"]["min_si_score"] == 0.6
        assert overridden["settings"]["min_si_score_mode"] == "override"
        assert overridden["min_si_score_auto"] == 0.41

        reset = service.set_min_si_score(None)
        assert reset["settings"]["min_si_score"] == 0.41
        assert reset["settings"]["min_si_score_mode"] == "auto"
        assert reset["settings"]["min_si_score_override"] is None
