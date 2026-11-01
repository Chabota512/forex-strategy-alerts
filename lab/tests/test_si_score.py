from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lab.si_beliefs import score_belief_rules
from lab.si_cases import SICaseStore


class SystemIntelligenceScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = {
            "case_type": "real",
            "features": {
                "direction": "BUY",
                "cct": "transition",
                "mtf_trend_agree": False,
            },
        }
        self.rule = {
            "id": "countertrend_consolidation",
            "samples": 30,
            "hit_rate": 0.8,
            "expectancy": 0.8,
            "weight": 0.8,
        }

    def test_score_is_bounded_and_shrinks_low_sample_evidence(self) -> None:
        baseline = {"baseline_hit_rate": 0.5, "baseline_expectancy": 0.0}
        high_sample_score = score_belief_rules(
            self.case,
            [self.rule],
            **baseline,
        )
        low_sample_score = score_belief_rules(
            self.case,
            [{**self.rule, "samples": 1}],
            **baseline,
        )
        neutral_score = score_belief_rules(
            {"case_type": "real", "features": {"cct": "uptrend"}},
            [self.rule],
            **baseline,
        )

        self.assertGreater(high_sample_score, low_sample_score)
        self.assertGreater(high_sample_score, 0.5)
        self.assertEqual(neutral_score, 0.5)
        self.assertTrue(0 <= high_sample_score <= 1)

    def test_scoring_snapshot_is_shared_by_batch_and_session_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SICaseStore(
                cases_path=Path(temporary) / "cases.jsonl",
                state_path=Path(temporary) / "beliefs.json",
            )
            belief_state = {
                "rules": [self.rule],
                "overall": {"hit_rate": 0.5, "expectancy": 0.0},
            }
            alert = {
                "direction": "BUY",
                "cct": "transition",
                "mtf_trend_agree": False,
                "swing_reversal": False,
            }
            fingerprint = {"consecutive_lows_reversal": False}

            batch_score = store.score_alert(alert, fingerprint, belief_state)
            session_provider = lambda row, shape: store.score_alert(
                row, shape, belief_state
            )
            session_score = session_provider(alert, fingerprint)

            self.assertEqual(session_score, batch_score)
            self.assertGreater(session_score, 0.5)


if __name__ == "__main__":
    unittest.main()
