from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from lab.alert_policy import passthrough_policy
from lab.replay import ReplaySession
from lab.si_manual_cases import read_manual_cases, read_manual_decisions

SPEC_PATH = Path(__file__).parents[1] / "specs" / "seed_v1.5.0.json"


def _record(index: int, open_: str, high: str, low: str, close: str) -> dict:
    return {
        "time": f"2025-01-01T00:0{index}:00Z",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def _alert(alert_id: str, direction: str) -> dict:
    return {
        "alert_id": alert_id,
        "symbol": "XAUUSD",
        "time": "2025-01-01T00:00:00Z",
        "tf": "M1",
        "direction": direction,
        "alert_type": f"{direction}-A",
        "strength_score": 0.7,
        "strength_label": "STRONG",
        "cct": "uptrend",
        "cst_v2_state": "uptrend",
        "candle": {
            "time": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100",
        },
        "previous_candle": {
            "time": "2024-12-31T23:59:00Z",
            "open": "99",
            "high": "100",
            "low": "98",
            "close": "99",
        },
        "formation_fingerprint": {"window": 20, "direction": direction},
    }


def test_take_and_skip_persist_comments_fingerprints_and_manual_trade() -> None:
    with TemporaryDirectory() as temp_dir:
        cases_path = Path(temp_dir) / "owner-decisions.jsonl"
        records = [
            _record(0, "100", "101", "99", "100"),
            _record(1, "100", "101", "99", "100"),
        ]
        spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        spec["instruments"] = ["XAUUSD"]
        spec["timeframes"] = ["M1"]
        session = ReplaySession(
            session_id="gold-manual-test",
            run_id="fixture-run",
            timeframe="M1",
            from_value="2025-01-01",
            to_value="2025-01-01",
            records=records,
            source="fixture",
            source_sha256=hashlib.sha256(b"manual fixture").hexdigest(),
            spec=spec,
            settings_fingerprint="fixture-settings",
            simulation_settings={
                "enabled": True,
                "startingBalance": 1000,
                "riskPercent": 1,
                "exitModel": "candle_geometry",
                "stopBodyWeight": 1,
                "stopWickMultiplier": 2,
                "targetRangeMultiplier": 3,
            },
            alert_policy=passthrough_policy(),
            manual_mode=True,
            manual_cases_path=cases_path,
        )
        session.simulation.process_candle(0)
        take_alert = _alert("take-alert", "SELL")
        skip_alert = _alert("skip-alert", "BUY")
        finalized_alert = _alert("finalized-alert", "SELL")
        session.alerts.extend(
            [(0, take_alert), (0, skip_alert), (0, finalized_alert)]
        )
        session.computed_through = 0

        taken = session.decide_manually(
            candle_index=0,
            action="take",
            alert_id="take-alert",
            direction="BUY",
            comment="Take the long setup",
        )
        skipped = session.decide_manually(
            candle_index=0,
            action="skip",
            alert_id="skip-alert",
            comment="Wait for confirmation",
        )
        finalized = session.decide_manually(candle_index=0, action="finalize")
        session.simulation.process_candle(1)

        for response in (taken, skipped, finalized):
            json.dumps(response, allow_nan=False)

        trade = session.simulation.trades_at(1)[0]
        assert trade["direction"] == "BUY"
        assert trade["entryIndex"] == 1
        assert trade["entryPrice"] == 100
        assert trade["source"] == "manual"
        assert trade["comment"] == "Take the long setup"
        assert taken["manualDecisions"][0]["decision"] == "take"
        assert skipped["manualDecisions"][0]["decision"] == "skip"
        assert finalized["manualDecisions"][0]["alertId"] == "finalized-alert"
        assert finalized["manualDecisions"][0]["decision"] == "skip"

        decisions = read_manual_decisions(cases_path)
        assert len(decisions) == 3
        assert {row["decision"] for row in decisions} == {"take", "skip"}
        assert {
            row["comment"]
            for row in decisions
            if row["comment"] is not None
        } == {
            "Take the long setup",
            "Wait for confirmation",
        }
        assert decisions[-1]["comment"] is None
        assert all(row["source"] == "manual" for row in decisions)
        assert all(len(row["formationFingerprint"]) == 64 for row in decisions)
        assert all(row["featureContext"]["symbol"] == "XAUUSD" for row in decisions)
        cases = {row["alertId"]: row for row in read_manual_cases(cases_path)}
        assert cases["take-alert"]["tradeDirection"] == "BUY"
        assert "tradeOutcome" not in cases["take-alert"]
