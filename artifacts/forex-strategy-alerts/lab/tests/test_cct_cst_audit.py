import json
import unittest
from pathlib import Path

from lab.engine import Candle, classify_cct, compute_cst_legacy
from lab.replay import StreamingCST


EXPECTED_PRODUCTION_DIVERGENCES = {
    "clean_higher_highs_higher_lows": {
        "cct": [4, 5, 6],
        "cst": [6],
    },
    "clean_lower_highs_lower_lows": {
        "cct": [4, 5, 6],
        "cst": [6],
    },
    "mixed_swings_remain_ranging": {
        "cct": [],
        "cst": [],
    },
    "close_confirmed_choch_and_reversal": {
        "cct": [4, 5, 6, 7, 8, 9, 10, 12],
        "cst": [6, 7, 8, 9, 10, 11, 12],
    },
}


class CctCstAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_path = Path(__file__).with_name("cct_cst_audit_fixtures.json")
        cls.fixture_file = json.loads(fixture_path.read_text(encoding="utf-8"))
        spec_path = Path(__file__).parents[1] / "specs" / "seed_v1.5.0.json"
        cls.spec = json.loads(spec_path.read_text(encoding="utf-8"))

    def _audit_fixture(self, fixture_id):
        fixture = next(
            item
            for item in self.fixture_file["fixtures"]
            if item["id"] == fixture_id
        )
        cct_settings = self.spec["cct"]
        cst_settings = self.spec["cst"]
        self.assertEqual(cct_settings["window"], 15)
        self.assertEqual(cct_settings["swing_neighbors"], 2)
        self.assertEqual(cst_settings["major_swing_neighbors"], 5)
        self.assertEqual(cst_settings["break_basis"], "close")

        candles = [
            Candle.from_values(
                f"2025-01-01T00:{index:02d}:00Z",
                *row,
            )
            for index, row in enumerate(fixture["candles"])
        ]
        self.assertEqual(len(fixture["expected_cct"]), len(candles))
        self.assertEqual(len(fixture["expected_cst"]), len(candles))

        cct_states = [
            classify_cct(
                candles[: index + 1],
                window=int(cct_settings["window"]),
                swing_neighbors=int(cct_settings["swing_neighbors"]),
            )
            for index in range(len(candles))
        ]

        streaming_cst = StreamingCST(
            swing_neighbors=int(cst_settings["major_swing_neighbors"]),
            break_basis=str(cst_settings["break_basis"]),
        )
        cst_states = []
        for index, candle in enumerate(candles):
            streaming_state = streaming_cst.update(candle)
            engine_prefix_state = compute_cst_legacy(
                candles[: index + 1],
                swing_neighbors=int(cst_settings["major_swing_neighbors"]),
                break_basis=str(cst_settings["break_basis"]),
            )[-1]
            self.assertEqual(
                streaming_state,
                engine_prefix_state,
                f"{fixture_id} CST streaming mismatch at candle {index}",
            )
            cst_states.append(streaming_state)

        # These fixtures are too short to confirm two swings per side for CCT
        # or any five-neighbor major swing for CST under the frozen spec.
        self.assertEqual(cct_states, ["ranging"] * len(candles))
        self.assertEqual(cst_states, ["undefined"] * len(candles))

        divergences = {
            "cct": [
                index
                for index, (expected, actual) in enumerate(
                    zip(fixture["expected_cct"], cct_states)
                )
                if expected != actual
            ],
            "cst": [
                index
                for index, (expected, actual) in enumerate(
                    zip(fixture["expected_cst"], cst_states)
                )
                if expected != actual
            ],
        }
        self.assertEqual(divergences, EXPECTED_PRODUCTION_DIVERGENCES[fixture_id])

    def test_clean_higher_highs_higher_lows(self):
        self._audit_fixture("clean_higher_highs_higher_lows")

    def test_clean_lower_highs_lower_lows(self):
        self._audit_fixture("clean_lower_highs_lower_lows")

    def test_mixed_swings_remain_ranging(self):
        self._audit_fixture("mixed_swings_remain_ranging")

    def test_close_confirmed_choch_and_reversal(self):
        self._audit_fixture("close_confirmed_choch_and_reversal")


if __name__ == "__main__":
    unittest.main()
