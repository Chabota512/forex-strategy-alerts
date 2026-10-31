from __future__ import annotations

import unittest

from lab import evolve
from lab.alert_policy import (
    load_policy,
    passthrough_policy,
    policy_rejection_reasons,
    validate_policy_settings,
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

    def test_legacy_variant_b_false_cannot_disable_merged_variant(self) -> None:
        policy = passthrough_policy()
        policy["mode"] = "active"
        policy["settings"] = validate_policy_settings({
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
