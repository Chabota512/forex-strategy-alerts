from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from lab import evolve
from lab.alert_policy import (
    freeze_effective_policy_for_replay,
    load_policy,
    passthrough_policy,
    policy_rejection_reasons,
    set_policy_min_si_score,
    validate_policy_settings,
    write_promoted_policy,
)


class AlertPolicyTests(unittest.TestCase):
    def test_repository_policy_starts_in_passthrough_mode(self) -> None:
        self.assertEqual(load_policy(evolve.DEFAULT_POLICY_PATH), passthrough_policy())

    def test_passthrough_policy_never_rejects_alerts(self) -> None:
        reasons = policy_rejection_reasons(
            passthrough_policy(),
            {
                "alert_type": "M1-B",
                "direction": "BUY",
                "strength_label": "WEAK",
                "engulfing": False,
            },
            timeframe="M1",
            candle_index=50,
            wick_pressure="down-pressure",
            previous_allowed_index=49,
        )

        self.assertEqual(reasons, [])

    def test_owner_score_threshold_filters_even_in_passthrough_mode(self) -> None:
        policy = passthrough_policy()
        policy["settings"]["min_si_score"] = 0.6
        reasons = policy_rejection_reasons(
            policy,
            {"direction": "BUY", "si_score": 0.42},
            timeframe="M5",
            candle_index=10,
            wick_pressure="balanced",
            previous_allowed_index=None,
        )
        self.assertEqual(
            reasons,
            ["si_score < 0.60: 0.42 is below minimum"],
        )
        policy["settings"]["min_si_score"] = 0
        self.assertEqual(
            policy_rejection_reasons(
                policy,
                {"direction": "BUY", "si_score": 0.1},
                timeframe="M5",
                candle_index=10,
                wick_pressure="balanced",
                previous_allowed_index=None,
            ),
            [],
        )

    def test_legacy_nonzero_score_becomes_an_owner_override(self) -> None:
        legacy = dict(passthrough_policy()["settings"])
        legacy.pop("min_si_score_mode")
        legacy.pop("min_si_score_override")
        legacy["min_si_score"] = 0.65

        normalized = validate_policy_settings(legacy)

        self.assertEqual(normalized["min_si_score_mode"], "override")
        self.assertEqual(normalized["min_si_score_override"], 0.65)

    def test_replay_freezes_effective_score_without_mutating_auto_policy(self) -> None:
        policy = passthrough_policy()
        policy["settings"]["min_si_score"] = 0.41
        runtime = freeze_effective_policy_for_replay(policy, symbol="XAUUSD")

        self.assertEqual(policy["settings"]["min_si_score_mode"], "auto")
        self.assertEqual(runtime["settings"]["min_si_score"], 0.41)
        self.assertEqual(runtime["settings"]["min_si_score_mode"], "override")
        self.assertEqual(runtime["settings"]["min_si_score_override"], 0.41)

        other_symbol = freeze_effective_policy_for_replay(policy, symbol="EURUSD")
        self.assertEqual(other_symbol["settings"]["min_si_score"], 0.0)
        self.assertEqual(other_symbol["settings"]["min_si_score_override"], 0.0)

    def test_owner_score_setting_is_versioned_and_survives_policy_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current_path = root / "policy_current.json"
            current_path.write_text(json.dumps(passthrough_policy()), encoding="utf-8")
            updated = set_policy_min_si_score(
                current_path=current_path,
                min_si_score=0.65,
                versions_dir=root / "versions",
            )
            self.assertEqual(updated["version"], 2)
            self.assertEqual(updated["settings"]["min_si_score"], 0.65)
            self.assertEqual(updated["settings"]["min_si_score_mode"], "override")
            self.assertEqual(updated["settings"]["min_si_score_override"], 0.65)
            promoted, _archive = write_promoted_policy(
                current_path=current_path,
                history_dir=root / "history",
                settings={
                    **updated["settings"],
                    "min_si_score": 0.0,
                },
                champion_version="test-champion",
                promoted_at_utc="2025-01-01T00:00:00Z",
                versions_dir=root / "versions",
            )
            self.assertEqual(promoted["settings"]["min_si_score"], 0.65)
            self.assertEqual(promoted["settings"]["min_si_score_mode"], "override")

            reset = set_policy_min_si_score(
                current_path=current_path,
                min_si_score=None,
                versions_dir=root / "versions",
            )
            self.assertEqual(reset["version"], 4)
            self.assertEqual(reset["settings"]["min_si_score"], 0.0)
            self.assertEqual(reset["settings"]["min_si_score_mode"], "auto")
            self.assertIsNone(reset["settings"]["min_si_score_override"])

    def test_active_policy_reports_each_rejecting_setting(self) -> None:
        policy = passthrough_policy()
        policy["mode"] = "active"
        policy["settings"] = {
            "min_strength": "STRONG",
            "require_wp_agree": True,
            "require_engulf": True,
            "timeframes": ["M15"],
            "exit_model": "swept_level_atr",
            "cooldown_candles": 3,
        }

        reasons = policy_rejection_reasons(
            policy,
            {
                "alert_type": "M5-B",
                "direction": "BUY",
                "strength_label": "WEAK",
                "engulfing": False,
            },
            timeframe="M5",
            candle_index=11,
            wick_pressure="balanced",
            previous_allowed_index=9,
        )

        self.assertEqual(
            reasons,
            [
                "timeframes=M15: M5 is excluded",
                "min_strength=STRONG: alert strength is WEAK",
                "require_wp_agree=true: balanced does not agree with BUY",
                "require_engulf=true: alert is not engulfing",
                "cooldown_candles=3: previous allowed alert was 2 candle(s) ago",
            ],
        )

    def test_legacy_variant_b_setting_is_rejected_for_merged_variant(self) -> None:
        policy = passthrough_policy()
        policy["mode"] = "active"
        with self.assertRaisesRegex(ValueError, "supported whitelist"):
            validate_policy_settings({
                **policy["settings"],
                "variant_b": False,
            })
        self.assertNotIn("variant_b", policy["settings"])

        reasons = policy_rejection_reasons(
            policy,
            {
                "alert_type": "M5-B",
                "direction": "BUY",
                "strength_label": "NEUTRAL",
                "engulfing": False,
            },
            timeframe="M5",
            candle_index=10,
            wick_pressure="balanced",
            previous_allowed_index=None,
        )
        self.assertEqual(reasons, [])


if __name__ == "__main__":
    unittest.main()
