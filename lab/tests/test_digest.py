from __future__ import annotations

import unittest

from lab.digest import build_digest


class DigestRetentionTests(unittest.TestCase):
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
            max_tokens=400,
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
        self.assertLessEqual(digest["estimated_tokens"], 400)


if __name__ == "__main__":
    unittest.main()
