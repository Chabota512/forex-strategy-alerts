import json
from decimal import Decimal
from pathlib import Path
import unittest

from lab.engine import (
    Candle,
    CandleDataError,
    body_engulfs_previous,
    classify_cct,
    compute_cst,
    detect_trigger,
    detect_trigger_b,
    score_alert,
    strength_for,
    smaller_body_note,
)

OWNER_FIXTURES_PATH = Path(__file__).parents[1] / "owner_labeled_fixtures.json"


def candle(open_: str, high: str, low: str, close: str, index: int = 0) -> Candle:
    return Candle.from_values(
        f"2026-01-01T00:{index:02d}:00+00:00",
        open_,
        high,
        low,
        close,
    )


class ConfirmedExampleTests(unittest.TestCase):
    """Representative OHLC fixtures for the eight labels in the spec's image log."""

    def test_image_png_is_sell(self):
        self.assertEqual(
            detect_trigger(candle("10", "12", "9", "11"), candle("11", "13", "10", "10.5")),
            "SELL",
        )

    def test_image_1_is_buy(self):
        self.assertEqual(
            detect_trigger(candle("11", "12", "9", "10"), candle("10", "11", "8", "10.5")),
            "BUY",
        )

    def test_image_2_is_no_alert_without_low_break(self):
        self.assertIsNone(
            detect_trigger(candle("11", "12", "9", "10"), candle("10", "12", "9.5", "10.5"))
        )

    def test_image_3_is_no_alert_without_high_break(self):
        self.assertIsNone(
            detect_trigger(candle("10", "12", "9", "11"), candle("11", "12", "9.5", "10.5"))
        )

    def test_image_4_directional_small_body_is_sell(self):
        self.assertEqual(
            detect_trigger(candle("10", "12", "9", "11"), candle("11", "13", "10.5", "10.99")),
            "SELL",
        )

    def test_image_5_marginal_low_break_is_buy(self):
        self.assertEqual(
            detect_trigger(candle("11", "12", "9", "10"), candle("10", "11", "8.999", "10.001")),
            "BUY",
        )

    def test_image_6_wide_high_break_is_sell(self):
        self.assertEqual(
            detect_trigger(candle("10", "12", "9", "11"), candle("11", "15", "10", "10.5")),
            "SELL",
        )

    def test_image_7_middle_green_then_break_is_sell(self):
        self.assertEqual(
            detect_trigger(candle("10", "12", "9", "11"), candle("11", "12.01", "10", "10.5")),
            "SELL",
        )

    def test_all_owner_labeled_fixtures_match_alert_and_note_exactly(self):
        with OWNER_FIXTURES_PATH.open("r", encoding="utf-8") as stream:
            fixture_file = json.load(stream, parse_float=Decimal)
        fixtures = fixture_file["fixtures"]
        self.assertEqual(len(fixtures), 8)

        for fixture in fixtures:
            with self.subTest(fixture=fixture["id"]):
                named_candles = {row["name"]: row for row in fixture["candles"]}
                previous_row = named_candles["prev"]
                current_row = named_candles["new"]
                previous = Candle.from_values(
                    "2026-01-01T00:00:00Z",
                    previous_row["open"],
                    previous_row["high"],
                    previous_row["low"],
                    previous_row["close"],
                )
                current = Candle.from_values(
                    "2026-01-01T00:01:00Z",
                    current_row["open"],
                    current_row["high"],
                    current_row["low"],
                    current_row["close"],
                )
                actual_alert = detect_trigger(previous, current)
                actual_note = smaller_body_note(previous, current)

                self.assertEqual(actual_alert, fixture["expected_alert"])
                self.assertEqual(actual_note, fixture["expected_note"])


class TriggerEdgeCaseTests(unittest.TestCase):
    def test_equal_high_is_not_a_sell_break(self):
        self.assertIsNone(
            detect_trigger(candle("10", "12", "9", "11"), candle("11", "12", "10", "10.5"))
        )

    def test_equal_low_is_not_a_buy_break(self):
        self.assertIsNone(
            detect_trigger(candle("11", "12", "9", "10"), candle("10", "11", "9", "10.5"))
        )

    def test_current_perfect_doji_does_not_alert(self):
        self.assertIsNone(
            detect_trigger(candle("10", "12", "9", "11"), candle("11", "13", "10", "11"))
        )

    def test_previous_doji_does_not_meet_either_color_condition(self):
        self.assertIsNone(
            detect_trigger(candle("10", "12", "9", "10"), candle("10", "13", "9.5", "9.8"))
        )

    def test_both_extremes_broken_sell_follows_close_direction(self):
        self.assertEqual(
            detect_trigger(candle("10", "12", "9", "11"), candle("11", "13", "8", "10.5")),
            "SELL",
        )

    def test_both_extremes_broken_buy_follows_close_direction(self):
        self.assertEqual(
            detect_trigger(candle("11", "12", "9", "10"), candle("10", "13", "8", "10.5")),
            "BUY",
        )

    def test_invalid_ohlc_is_rejected(self):
        with self.assertRaises(CandleDataError):
            candle("10", "9", "8", "10.5")

    def test_insufficient_cct_history_is_ranging(self):
        self.assertEqual(
            classify_cct([candle("10", "11", "9", "10.5") for _ in range(10)]),
            "ranging",
        )


class VariantBTriggerTests(unittest.TestCase):
    def test_sell_b_fires_on_a_strict_same_color_high_sweep(self):
        previous = candle("11", "12", "9", "10")
        current = candle("10", "12.01", "9.5", "9.5")

        self.assertEqual(detect_trigger_b(previous, current), "SELL-B")
        self.assertIsNone(detect_trigger(previous, current))

    def test_buy_b_fires_on_a_strict_same_color_low_sweep(self):
        previous = candle("10", "12", "9", "11")
        current = candle("11", "11.8", "8.99", "11.5")

        self.assertEqual(detect_trigger_b(previous, current), "BUY-B")
        self.assertIsNone(detect_trigger(previous, current))

    def test_equal_extreme_and_dojis_do_not_fire_variant_b(self):
        previous_red = candle("11", "12", "9", "10")
        equal_high_red = candle("10", "12", "9.5", "9.5")
        previous_green = candle("10", "12", "9", "11")
        equal_low_green = candle("11", "11.8", "9", "11.5")
        previous_doji = candle("10", "12", "9", "10")
        current_doji = candle("11", "13", "8", "11")

        self.assertIsNone(detect_trigger_b(previous_red, equal_high_red))
        self.assertIsNone(detect_trigger_b(previous_green, equal_low_green))
        self.assertIsNone(
            detect_trigger_b(previous_doji, candle("10", "13", "9.5", "9.8"))
        )
        self.assertIsNone(detect_trigger_b(previous_red, current_doji))

    def test_both_extremes_broken_follow_the_current_close_direction(self):
        previous_red = candle("11", "12", "9", "10")
        current_red = candle("10.5", "12.1", "8.9", "9.8")
        previous_green = candle("10", "12", "9", "11")
        current_green = candle("10.5", "12.1", "8.9", "10.8")

        self.assertEqual(detect_trigger_b(previous_red, current_red), "SELL-B")
        self.assertEqual(detect_trigger_b(previous_green, current_green), "BUY-B")


class EngulfingTests(unittest.TestCase):
    def test_buy_body_engulf_includes_equal_body_boundaries(self):
        previous = candle("12", "13", "9", "10")
        current = candle("10", "12", "9.5", "12")

        self.assertTrue(body_engulfs_previous(previous, current, "BUY"))

    def test_sell_body_engulf_includes_equal_body_boundaries(self):
        previous = candle("10", "13", "9", "12")
        current = candle("12", "12.5", "9.5", "10")

        self.assertTrue(body_engulfs_previous(previous, current, "SELL"))

    def test_body_that_does_not_cover_both_previous_body_edges_is_not_engulfing(self):
        previous = candle("12", "13", "9", "10")
        current = candle("10.01", "12", "9.5", "12")

        self.assertFalse(body_engulfs_previous(previous, current, "BUY"))


def reward_bar(high: str, low: str, index: int = 0) -> Candle:
    return Candle.from_values(
        f"2026-02-01T00:{index:02d}:00Z",
        "100",
        high,
        low,
        "100",
    )


class RewardScoringTests(unittest.TestCase):
    def setUp(self):
        self.entry = Decimal("100")
        self.d_min = Decimal("1")
        self.a_max = Decimal("0.8")

    def score(self, direction, bars, **settings):
        return score_alert(
            direction,
            self.entry,
            bars,
            d_min=self.d_min,
            a_max=self.a_max,
            **settings,
        )

    def test_no_target_is_negative_one(self):
        score, status = self.score("BUY", [reward_bar("100.9", "99.5") for _ in range(12)])
        self.assertEqual((score, status), (Decimal("-1"), "SCORED"))

    def test_target_first_within_six_candles_gets_speed_bonus(self):
        bars = [reward_bar("101", "99.5")] + [reward_bar("100.2", "99.5") for _ in range(11)]
        self.assertEqual(self.score("BUY", bars), (Decimal("1.5"), "SCORED"))

    def test_target_first_after_speed_window_scores_one(self):
        bars = [reward_bar("100.2", "99.5") for _ in range(6)]
        bars.extend([reward_bar("101", "99.5")] + [reward_bar("100.2", "99.5") for _ in range(5)])
        self.assertEqual(self.score("BUY", bars), (Decimal("1"), "SCORED"))

    def test_adverse_first_then_target_gets_half_point(self):
        bars = [reward_bar("100.2", "99.19"), reward_bar("101", "99.5")]
        bars.extend([reward_bar("100.2", "99.5") for _ in range(10)])
        self.assertEqual(self.score("BUY", bars), (Decimal("0.5"), "SCORED"))

    def test_adverse_first_keeps_follow_through_bonus_after_target(self):
        bars = [
            reward_bar("100.2", "99.19"),
            reward_bar("101", "99.5"),
            reward_bar("103.2", "99.5"),
        ]
        bars.extend([reward_bar("100.2", "99.5") for _ in range(9)])
        self.assertEqual(self.score("BUY", bars), (Decimal("0.7"), "SCORED"))

    def test_follow_through_bonus_is_capped(self):
        bars = [reward_bar("101", "99.5"), reward_bar("106", "99.5")]
        bars.extend([reward_bar("100.2", "99.5") for _ in range(10)])
        self.assertEqual(self.score("BUY", bars), (Decimal("2.0"), "SCORED"))

    def test_same_candle_tie_defaults_to_adverse_first(self):
        bars = [reward_bar("101", "99.19")] + [reward_bar("100.2", "99.5") for _ in range(11)]
        self.assertEqual(self.score("BUY", bars), (Decimal("0.5"), "SCORED"))

    def test_same_candle_tie_can_be_configured_target_first(self):
        bars = [reward_bar("101", "99.19")] + [reward_bar("100.2", "99.5") for _ in range(11)]
        self.assertEqual(
            self.score("BUY", bars, same_candle_tie="target_first"),
            (Decimal("1.5"), "SCORED"),
        )

    def test_threshold_edges_are_inclusive_for_target_and_strict_for_adverse(self):
        bars = [reward_bar("101", "99.2")] + [reward_bar("100.2", "99.5") for _ in range(11)]
        self.assertEqual(self.score("BUY", bars), (Decimal("1.5"), "SCORED"))

    def test_sell_scoring_mirrors_buy(self):
        bars = [reward_bar("100.2", "99")] + [reward_bar("100.5", "99.8") for _ in range(11)]
        self.assertEqual(self.score("SELL", bars), (Decimal("1.5"), "SCORED"))

    def test_insufficient_lookahead_is_not_scored(self):
        self.assertEqual(
            self.score("BUY", [reward_bar("101", "99.5") for _ in range(11)]),
            (None, "INSUFFICIENT_LOOKAHEAD"),
        )


class CstTests(unittest.TestCase):
    def structural_candle(self, close: str, *, high: str | None = None, low: str | None = None, index: int = 0):
        value = Decimal(close)
        return Candle.from_values(
            f"2026-03-01T00:{index:02d}:00Z",
            value,
            Decimal(high) if high is not None else value + Decimal("0.1"),
            Decimal(low) if low is not None else value - Decimal("0.1"),
            value,
        )

    def test_first_break_of_confirmed_high_sets_uptrend_from_undefined(self):
        values = ["5", "10", "6", "11"]
        candles = [
            self.structural_candle(
                value,
                high=str(Decimal(value) + Decimal("0.1")),
                low="1",
                index=index,
            )
            for index, value in enumerate(values)
        ]
        self.assertEqual(compute_cst(candles, swing_neighbors=1)[-1], "uptrend")

    def test_break_basis_switches_between_close_and_wick(self):
        candles = [
            self.structural_candle("5", low="4.9", index=0),
            self.structural_candle("10", low="9.9", index=1),
            self.structural_candle("6", low="5.9", index=2),
            self.structural_candle("9", high="10.2", low="8.9", index=3),
        ]
        self.assertEqual(compute_cst(candles, swing_neighbors=1, break_basis="close")[-1], "undefined")
        self.assertEqual(compute_cst(candles, swing_neighbors=1, break_basis="wick")[-1], "uptrend")

    def test_choch_waits_for_a_post_choch_swing_before_confirming(self):
        values = ["5", "7", "9", "7", "6", "8", "10", "7", "5", "7", "5.5", "7", "5"]
        candles = [self.structural_candle(value, index=index) for index, value in enumerate(values)]
        states = compute_cst(candles, swing_neighbors=1, break_basis="close")
        self.assertEqual(states[6], "uptrend")
        self.assertEqual(states[8], "transition")
        self.assertEqual(states[11], "transition")
        self.assertEqual(states[12], "downtrend")

    def test_saved_prior_level_can_resume_old_trend_during_transition(self):
        values = ["5", "7", "9", "7", "6", "8", "10", "7", "5", "11"]
        candles = [self.structural_candle(value, index=index) for index, value in enumerate(values)]
        states = compute_cst(candles, swing_neighbors=1, break_basis="close")
        self.assertEqual(states[8], "transition")
        self.assertEqual(states[9], "uptrend")

    def test_cst_prefixes_do_not_use_future_candles(self):
        values = ["5", "7", "9", "7", "6", "8", "10", "7", "5", "7", "5.5", "7", "5"]
        candles = [self.structural_candle(value, index=index) for index, value in enumerate(values)]
        full = compute_cst(candles, swing_neighbors=1)
        prefix_states = [
            compute_cst(candles[: index + 1], swing_neighbors=1)[-1]
            for index in range(len(candles))
        ]
        self.assertEqual(prefix_states, full)

    def test_cct_cst_audit_fixtures_match_every_candle(self):
        fixture_path = Path(__file__).with_name("cct_cst_audit_fixtures.json")
        fixture_file = json.loads(fixture_path.read_text(encoding="utf-8"))
        neighbors = fixture_file["swing_neighbors"]
        for fixture in fixture_file["fixtures"]:
            with self.subTest(fixture=fixture["id"]):
                candles = [
                    Candle.from_values(
                        f"2025-01-01T00:{index:02d}:00Z",
                        *row,
                    )
                    for index, row in enumerate(fixture["candles"])
                ]
                cct_states = [
                    classify_cct(
                        candles[: index + 1],
                        window=15,
                        swing_neighbors=neighbors,
                    )
                    for index in range(len(candles))
                ]
                cst_states = compute_cst(
                    candles,
                    swing_neighbors=neighbors,
                    break_basis=fixture_file["break_basis"],
                )

                self.assertEqual(cct_states, fixture["expected_cct"])
                self.assertEqual(cst_states, fixture["expected_cst"])

    def test_strength_labels_do_not_suppress_weak_alerts(self):
        self.assertEqual(strength_for("BUY", "uptrend"), "STRONG")
        self.assertEqual(strength_for("BUY", "downtrend"), "WEAK")
        self.assertEqual(strength_for("SELL", "transition"), "NEUTRAL")
        self.assertEqual(strength_for("SELL", "undefined"), "NEUTRAL")


if __name__ == "__main__":
    unittest.main()
