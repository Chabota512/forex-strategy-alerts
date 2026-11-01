from __future__ import annotations

import unittest

from lab.digest import build_digest


class DigestRetentionTests(unittest.TestCase):
    def test_swing_version_agreement_is_a_digest_axis(self) -> None:
        digest = build_digest(
            [
                {"swing_version_agreement_pct": 100}
                for _ in range(30)
            ]
            + [
                {"swing_version_agreement_pct": 0}
                for _ in range(30)
            ],
            min_cell_size=30,
        )
        agreement_groups = [
            group
            for group in digest["groups"]
            if group["axis"] == "swing_version_agreement_pct"
        ]

        self.assertEqual(
            {(group["value"], group["n"]) for group in agreement_groups},
            {("0", 30), ("100", 30)},
        )

    def test_strength_score_is_a_rounded_digest_axis(self) -> None:
        digest = build_digest(
            [
                {"strength_score": "0.54321", "direction": "BUY"}
                for _ in range(30)
            ],
            min_cell_size=30,
        )
        score_groups = [
            group for group in digest["groups"] if group["axis"] == "strength_score"
        ]

        self.assertEqual(len(score_groups), 1)
        self.assertEqual(score_groups[0]["value"], "0.5")
        self.assertEqual(score_groups[0]["n"], 30)

    def test_true_mtf_agreement_cells_survive_token_truncation(self) -> None:
        snapshots = []
        for index in range(160):
            is_agreement_case = index < 3
            snapshots.append(
                {
                    "time": f"2025-01-{index % 28 + 1:02d}T00:00:00Z",
                    "period": f"unique-period-{index:03d}",
                    "direction": "BUY",
                    "variant": "A",
                    "tf": "M5",
                    "strength": "STRONG",
                    "wp_state": "balanced",
                    "engulf": False,
                    "cst": "uptrend",
                    "dc_state": "undefined",
                    "hier_state": "undefined",
                    "mtf_cst": "uptrend",
                    "mtf_cct": "uptrend",
                    "mtf_wick_pressure": "balanced",
                    "mtf_trend_agree": is_agreement_case,
                    "mtf_cct_agree": is_agreement_case,
                    "mtf_wp_agree": is_agreement_case,
                    "dual_tf_alert": is_agreement_case,
                    "sim_outcome": "WIN",
                    "r_multiple_achieved": "1.0",
                    "sim_r_after_costs": "0.5",
                }
            )

        digest = build_digest(
            snapshots,
            min_cell_size=1,
            max_tokens=650,
        )

        retained = {
            (group["axis"], group["value"])
            for group in digest["groups"]
            if group["axis"]
            in {
                "mtf_trend_agree",
                "mtf_wp_agree",
                "dual_tf_alert",
                "mtf_cct_agree",
            }
            and str(group["value"]).lower() == "true"
        }
        self.assertEqual(
            retained,
            {
                ("mtf_trend_agree", "True"),
                ("mtf_cct_agree", "True"),
                ("mtf_wp_agree", "True"),
                ("dual_tf_alert", "True"),
            },
        )
        self.assertTrue(
            all(
                group["n"] == 3
                for group in digest["groups"]
                if group["axis"]
                in {
                    "mtf_trend_agree",
                    "mtf_wp_agree",
                    "dual_tf_alert",
                    "mtf_cct_agree",
                }
                and str(group["value"]).lower() == "true"
            )
        )
        self.assertGreater(digest["truncated_cells"], 0)
        self.assertLessEqual(digest["estimated_tokens"], 650)

    def test_follow_and_fade_expectancy_respect_resolved_sample_floor(self) -> None:
        snapshots = []
        for variant, follow_count, fade_count in (
            ("FOLLOW_OK", 30, 29),
            ("FADE_OK", 29, 30),
        ):
            for index in range(30):
                followed = index < follow_count
                faded = index < fade_count
                snapshots.append(
                    {
                        "variant": variant,
                        "sim_outcome": "WIN" if followed else "OPEN",
                        "r_multiple_achieved": "1.0" if followed else None,
                        "sim_r_after_costs": "0.3" if followed else None,
                        "sim_fade_outcome": "LOSS" if faded else "OPEN",
                        "sim_fade_r_after_costs": "-0.1" if faded else None,
                    }
                )

        digest = build_digest(snapshots, min_cell_size=30, max_tokens=10_000)
        cells = {
            group["value"]: group
            for group in digest["groups"]
            if group["axis"] == "variant"
        }

        follow_ok = cells["FOLLOW_OK"]
        self.assertEqual(follow_ok["n"], 30)
        self.assertEqual(follow_ok["follow_vs_fade"]["follow_resolved"], 30)
        self.assertEqual(
            follow_ok["follow_vs_fade"]["follow_expectancy_after_costs"], 0.3
        )
        self.assertEqual(follow_ok["follow_vs_fade"]["fade_resolved"], 29)
        self.assertIsNone(
            follow_ok["follow_vs_fade"]["fade_expectancy_after_costs"]
        )

        fade_ok = cells["FADE_OK"]
        self.assertEqual(fade_ok["follow_vs_fade"]["follow_resolved"], 29)
        self.assertIsNone(
            fade_ok["follow_vs_fade"]["follow_expectancy_after_costs"]
        )
        self.assertEqual(fade_ok["follow_vs_fade"]["fade_resolved"], 30)
        self.assertEqual(
            fade_ok["follow_vs_fade"]["fade_expectancy_after_costs"], -0.1
        )


if __name__ == "__main__":
    unittest.main()
