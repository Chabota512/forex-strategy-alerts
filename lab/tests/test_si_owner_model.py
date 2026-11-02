from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from lab.alert_policy import passthrough_policy, policy_rejection_reasons
from lab.si_manual_cases import record_manual_decision, record_manual_outcome
from lab.si_owner_model import (
    fit_owner_model,
    load_owner_model,
    select_score,
)


def _context(direction: str) -> dict:
    return {
        "symbol": "XAUUSD",
        "direction": direction,
        "strength_score": 0.7,
        "strength_label": "STRONG",
        "cct": "uptrend",
        "cst_v2_state": "uptrend",
        "candle": {"open": "100", "high": "101", "low": "99", "close": "100"},
    }


def test_fixture_decision_log_moves_evidence_shrunk_weights_and_tracks_outcomes() -> None:
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "owner-decisions.jsonl"
        for index in range(60):
            direction = "BUY" if index % 2 == 0 else "SELL"
            decision = "take" if direction == "BUY" else "skip"
            case_id = f"case-{index}"
            record_manual_decision(
                path,
                case_id=case_id,
                session_id="fixture",
                alert_id=f"alert-{index}",
                candle_index=index,
                decision=decision,
                comment=f"fixture {decision}",
                formation={"index": index},
                feature_context=_context(direction),
                score_model="evidence",
                blend_weight=0.5,
                trade_direction=direction if decision == "take" else None,
            )
            if decision == "take":
                record_manual_outcome(
                    path,
                    case_id=case_id,
                    trade_id=f"trade-{index}",
                    net_r=1.0 if index % 4 == 0 else -0.5,
                    outcome="WIN" if index % 4 == 0 else "LOSS",
                )

        model = load_owner_model(path)

        assert model["trained"] is True
        assert model["qualified"] is True
        assert model["caseCount"] == 60
        assert model["ownerWeight"] == 2 / 3
        assert model["weights"]["direction"] > 0
        assert 0 < model["effectiveWeights"]["direction"] < model["weights"]["direction"]
        assert model["resolvedTakenCount"] == 30
        assert model["hitRate"] == 0.5
        assert model["expectancyR"] == 0.25


def test_model_selector_changes_min_si_score_gating() -> None:
    decisions = [
        {"decision": "skip", "featureContext": _context("BUY")}
        for _ in range(30)
    ]
    model = fit_owner_model(decisions)
    alert = _context("BUY")
    evidence_score = 0.8
    evidence, _, _ = select_score(
        alert,
        evidence_score=evidence_score,
        model_name="evidence",
        owner_model=model,
        blend_weight=0.5,
    )
    owner, _, _ = select_score(
        alert,
        evidence_score=evidence_score,
        model_name="owner",
        owner_model=model,
        blend_weight=0.5,
    )
    policy = passthrough_policy()
    policy["settings"]["min_si_score"] = 0.6
    evidence_rejections = policy_rejection_reasons(
        policy,
        {"si_score": evidence},
        timeframe="M5",
        candle_index=0,
        wick_pressure="balanced",
        previous_allowed_index=None,
    )
    owner_rejections = policy_rejection_reasons(
        policy,
        {"si_score": owner},
        timeframe="M5",
        candle_index=0,
        wick_pressure="balanced",
        previous_allowed_index=None,
    )

    assert evidence is not None and evidence >= 0.6
    assert owner is not None and owner < 0.6
    assert not evidence_rejections
    assert any("si_score <" in reason for reason in owner_rejections)
