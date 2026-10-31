from __future__ import annotations

from decimal import Decimal
from typing import Any
import unittest

from lab.simulation import (
    SIMULATION_COSTS,
    SimulationSettings,
    SimulationSession,
    simulate_batch,
)
from lab.instruments import AUDJPY_SIMULATION_COST, GBPJPY_SIMULATION_COST


def _record(
    index: int,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> dict[str, Any]:
    return {
        "time": f"2025-01-01T00:{index:02d}:00Z",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def _alert(
    alert_id: str,
    direction: str,
    *,
    low: str = "99",
    high: str = "101",
    strength: str = "STRONG",
) -> dict[str, Any]:
    return {
        "alert_id": alert_id,
        "symbol": "XAUUSD",
        "direction": direction,
        "alert_type": f"{direction}-A",
        "strength_label": strength,
        "candle": {"time": "2025-01-01T00:00:00Z", "open": "100", "high": high, "low": low, "close": "100"},
        "previous_candle": {"time": "2024-12-31T23:59:00Z", "open": "99", "high": "100", "low": "98", "close": "99"},
    }


def _settings(
    *,
    exit_model: str = "swept_level_atr",
    reward_multiple: float = 2,
    stop_body_weight: float = 1,
    stop_wick_multiplier: float = 2,
    target_range_multiplier: float = 3,
) -> dict[str, Any]:
    return {
        "enabled": True,
        "startingBalance": 10000,
        "riskPercent": 1,
        "rewardMultiple": reward_multiple,
        "exitModel": exit_model,
        "stopBodyWeight": stop_body_weight,
        "stopWickMultiplier": stop_wick_multiplier,
        "targetRangeMultiplier": target_range_multiplier,
    }


def _geometry_alert(alert_id: str, direction: str) -> dict[str, Any]:
    alert = _alert(alert_id, direction, low="98", high="102")
    alert["candle"]["open"] = "99" if direction == "BUY" else "101"
    alert["candle"]["close"] = "101" if direction == "BUY" else "99"
    return alert


def test_position_sizing_uses_next_open_and_atr_minimum_stop() -> None:
    records = [
        _record(0, "100", "101", "99", "100"),
        _record(1, "100", "102", "99", "101"),
    ]
    simulation = SimulationSession(records, settings=_settings())
    simulation.process_candle(0)
    simulation.queue_alert(0, _alert("buy-1", "BUY", low="99.8"))
    simulation.process_candle(1)

    trade = simulation.trades_at(1)[0]
    assert trade["entryIndex"] == 1
    assert trade["entryPrice"] == 100
    assert trade["entryTime"] == records[1]["time"]
    assert trade["riskAmount"] == 100
    assert trade["positionSize"] == 50
    assert trade["stopPrice"] == 98
    assert trade["takeProfitPrice"] == 104
    assert trade["outcome"] == "OPEN"


def test_same_candle_stop_and_target_resolves_as_stop_first() -> None:
    records = [
        _record(0, "100", "101", "99", "100"),
        _record(1, "100", "104", "98", "100"),
    ]
    simulation = SimulationSession(records, settings=_settings())
    simulation.process_candle(0)
    simulation.queue_alert(0, _alert("tie-1", "BUY"))
    simulation.process_candle(1)

    trade = simulation.trades_at(1)[0]
    assert trade["outcome"] == "LOSS"
    assert trade["exitReason"] == "SL"
    assert trade["exitPrice"] == 98
    assert trade["grossPnl"] == -100
    assert trade["cost"] == 17.5
    assert trade["netPnl"] == -117.5
    assert simulation.snapshot(1)["balance"] == 9882.5


def test_equity_curve_and_statistics_update_on_each_resolution() -> None:
    records = [
        _record(0, "100", "101", "99", "100"),
        _record(1, "100", "104", "99", "103"),
        _record(2, "100", "101", "99", "100"),
        _record(3, "100", "104", "97", "99"),
    ]
    alerts = {
        0: [_alert("buy-1", "BUY")],
        2: [_alert("sell-1", "SELL")],
    }
    simulation = SimulationSession(records, settings=_settings())
    for index in range(len(records)):
        simulation.process_candle(index)
        for alert in alerts.get(index, []):
            simulation.queue_alert(index, alert)

    snapshot = simulation.snapshot(3)
    assert snapshot["closedTradeCount"] == 2
    assert snapshot["openTradeCount"] == 0
    assert len(snapshot["equityCurve"]) == 4
    assert snapshot["equityCurve"][0]["equity"] == 10000
    assert snapshot["equityCurve"][1]["equity"] > 10000
    assert snapshot["balance"] == snapshot["equityCurve"][-1]["equity"]
    assert snapshot["stats"]["wins"] == 1
    assert snapshot["stats"]["losses"] == 1
    assert snapshot["stats"]["maxDrawdown"] > 0
    assert snapshot["stats"]["maxDrawdownPct"] > 0
    assert snapshot["stats"]["profitFactor"] is not None


def test_batch_and_incremental_sessions_produce_identical_trades_and_equity() -> None:
    records = [
        _record(0, "100", "101", "99", "100"),
        _record(1, "100", "104", "99", "103"),
        _record(2, "100", "101", "99", "100"),
        _record(3, "100", "104", "97", "99"),
    ]
    alerts = {
        0: [_alert("buy-1", "BUY")],
        2: [_alert("sell-1", "SELL")],
    }
    for settings in (
        _settings(),
        _settings(exit_model="candle_geometry"),
        _settings(
            exit_model="candle_geometry",
            stop_body_weight=1.5,
            stop_wick_multiplier=0.5,
            target_range_multiplier=2.5,
        ),
    ):
        batch = simulate_batch(records, alerts, settings=settings)

        incremental = SimulationSession(records, settings=settings)
        for index in range(len(records)):
            incremental.process_candle(index)
            for alert in alerts.get(index, []):
                incremental.queue_alert(index, alert)

        assert batch["trades"] == incremental.trades_at(3)
        assert batch["simulation"] == incremental.snapshot(3)


def test_candle_geometry_multiplier_settings_default_to_engine_constants() -> None:
    settings = SimulationSettings.from_payload(None)

    assert settings.stop_body_weight == Decimal("1")
    assert settings.stop_wick_multiplier == Decimal("2")
    assert settings.target_range_multiplier == Decimal("3")
    assert settings.to_dict()["stopBodyWeight"] == 1
    assert settings.to_dict()["stopWickMultiplier"] == 2
    assert settings.to_dict()["targetRangeMultiplier"] == 3


def test_candle_geometry_multipliers_change_fixture_trade_sl_and_tp() -> None:
    records = [
        _record(0, "100", "101", "99", "100"),
        _record(1, "100", "101", "99", "100"),
    ]
    simulation = SimulationSession(
        records,
        settings=_settings(
            exit_model="candle_geometry",
            stop_body_weight=1.5,
            stop_wick_multiplier=0.5,
            target_range_multiplier=2.5,
        ),
    )
    simulation.process_candle(0)
    simulation.queue_alert(0, _geometry_alert("weighted-buy", "BUY"))
    simulation.process_candle(1)

    trade = simulation.trades_at(1)[0]
    assert trade["entryPrice"] == 100
    assert trade["stopPrice"] == 96.5
    assert trade["takeProfitPrice"] == 110


def test_candle_geometry_multipliers_must_be_positive_finite_decimals() -> None:
    for key in ("stopBodyWeight", "stopWickMultiplier", "targetRangeMultiplier"):
        for invalid in (0, -0.1, "NaN", "Infinity"):
            try:
                SimulationSettings.from_payload(
                    {"exitModel": "candle_geometry", key: invalid}
                )
            except ValueError:
                continue
            raise AssertionError(f"{key} accepted invalid value {invalid!r}")


def test_candle_geometry_exit_prices_are_exact_mirrors_for_buy_and_sell() -> None:
    records = [
        _record(0, "100", "101", "99", "100"),
        _record(1, "100", "101", "99", "100"),
    ]
    opened: dict[str, dict[str, Any]] = {}
    for direction in ("BUY", "SELL"):
        simulation = SimulationSession(
            records,
            settings=_settings(exit_model="candle_geometry"),
        )
        simulation.process_candle(0)
        simulation.queue_alert(0, _geometry_alert(f"{direction.lower()}-geometry", direction))
        simulation.process_candle(1)
        opened[direction] = simulation.trades_at(1)[0]

    buy = opened["BUY"]
    sell = opened["SELL"]
    assert buy["entryPrice"] == sell["entryPrice"] == 100
    assert (buy["stopPrice"], buy["takeProfitPrice"]) == (96, 112)
    assert (sell["stopPrice"], sell["takeProfitPrice"]) == (104, 88)
    assert buy["stopPrice"] + sell["stopPrice"] == 200
    assert buy["takeProfitPrice"] + sell["takeProfitPrice"] == 200
    assert buy["exitModel"] == sell["exitModel"] == "candle_geometry"
    assert buy["slInsideSweepTip"] is False
    assert sell["slInsideSweepTip"] is False


def test_candle_geometry_gap_sets_sweep_tip_flag_without_clamping_or_crashing() -> None:
    records = [
        _record(0, "100", "101", "99", "100"),
        _record(1, "110", "111", "109", "110"),
    ]
    simulation = SimulationSession(
        records,
        settings=_settings(exit_model="candle_geometry"),
    )
    simulation.process_candle(0)
    simulation.queue_alert(0, _geometry_alert("gap-buy", "BUY"))
    simulation.process_candle(1)

    trade = simulation.trades_at(1)[0]
    assert trade["stopPrice"] == 106
    assert trade["takeProfitPrice"] == 122
    assert trade["stopPrice"] != 98
    assert trade["slInsideSweepTip"] is True
    assert trade["outcome"] == "OPEN"


def test_candle_geometry_and_swept_exit_models_resolve_on_their_own_targets() -> None:
    records = [
        _record(0, "100", "101", "99", "100"),
        _record(1, "100", "101", "99", "100"),
        _record(2, "100", "105", "99", "102"),
        _record(3, "102", "113", "100", "110"),
    ]
    results: dict[str, dict[str, Any]] = {}
    for exit_model in ("swept_level_atr", "candle_geometry"):
        simulation = SimulationSession(records, settings=_settings(exit_model=exit_model))
        simulation.process_candle(0)
        simulation.queue_alert(0, _geometry_alert(f"{exit_model}-buy", "BUY"))
        for index in range(1, len(records)):
            simulation.process_candle(index)
        results[exit_model] = simulation.trades_at(3)[0]

    swept = results["swept_level_atr"]
    geometry = results["candle_geometry"]
    assert (swept["takeProfitPrice"], swept["exitIndex"], swept["exitReason"]) == (
        104, 2, "TP"
    )
    assert (geometry["takeProfitPrice"], geometry["exitIndex"], geometry["exitReason"]) == (
        112, 3, "TP"
    )


def test_reward_multiple_only_changes_swept_level_atr_targets() -> None:
    records = [
        _record(0, "100", "101", "99", "100"),
        _record(1, "100", "101", "99", "100"),
    ]
    targets: dict[tuple[str, float], float] = {}
    for exit_model in ("swept_level_atr", "candle_geometry"):
        for reward_multiple in (1, 4):
            simulation = SimulationSession(
                records,
                settings=_settings(
                    exit_model=exit_model,
                    reward_multiple=reward_multiple,
                    stop_body_weight=5,
                    stop_wick_multiplier=7,
                    target_range_multiplier=11,
                ),
            )
            simulation.process_candle(0)
            simulation.queue_alert(
                0, _geometry_alert(f"{exit_model}-{reward_multiple}", "BUY")
            )
            simulation.process_candle(1)
            targets[(exit_model, reward_multiple)] = simulation.trades_at(1)[0][
                "takeProfitPrice"
            ]

    assert targets[("swept_level_atr", 1)] == 102
    assert targets[("swept_level_atr", 4)] == 108
    assert targets[("candle_geometry", 1)] == 144
    assert targets[("candle_geometry", 4)] == 144


def test_simulation_filters_and_named_instrument_costs() -> None:
    records = [
        _record(0, "100", "101", "99", "100"),
        _record(1, "100", "102", "99", "101"),
    ]
    simulation = SimulationSession(records, settings=_settings())
    simulation.process_candle(0)
    simulation.queue_alert(
        0,
        _alert("weak-buy", "BUY", strength="WEAK"),
        {"direction": "BUY", "strength": "STRONG", "engulfingOnly": False},
    )
    engulfing_alert = _alert("engulfing-buy", "BUY")
    engulfing_alert["candle"] = {
        "time": "2025-01-01T00:00:00Z",
        "open": "98",
        "high": "102",
        "low": "97",
        "close": "101",
    }
    engulfing_alert["previous_candle"] = {
        "time": "2024-12-31T23:59:00Z",
        "open": "100",
        "high": "101",
        "low": "99",
        "close": "99",
    }
    simulation.queue_alert(
        0,
        engulfing_alert,
        {"direction": "BUY", "strength": "STRONG", "engulfingOnly": True},
    )
    simulation.process_candle(1)

    assert [trade["alertId"] for trade in simulation.trades_at(1)] == [
        "engulfing-buy"
    ]
    assert SIMULATION_COSTS == {
        "XAUUSD": Decimal("0.35"),
        "GBPUSD": Decimal("0.0001"),
        "USDJPY": Decimal("0.0001"),
        "GBPJPY": Decimal("0.015"),
        "AUDJPY": Decimal("0.010"),
    }
    assert GBPJPY_SIMULATION_COST == Decimal("0.015")
    assert AUDJPY_SIMULATION_COST == Decimal("0.010")
    assert SimulationSession([], symbol="GBPJPY").symbol == "GBPJPY"
    assert SimulationSession([], symbol="AUDJPY").symbol == "AUDJPY"


class _FunctionTest(unittest.TestCase):
    def __init__(self, function):
        super().__init__("runTest")
        self.function = function

    def runTest(self) -> None:
        self.function()


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            suite.addTest(_FunctionTest(function))
    return suite
