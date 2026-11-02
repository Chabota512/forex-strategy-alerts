from __future__ import annotations

import threading
from typing import Any
from unittest.mock import patch

from lab.autoevolve import AutoevolveManager
from lab.evolve_jobs import EvolveJobManager
from lab.si_beliefs import adjust_tunables, initial_tunables, validate_tunables
from lab.si_cases import (
    SICaseStore,
    build_formation_fingerprint,
    cases_from_snapshot,
)


def _case(
    index: int,
    *,
    net_r: float,
    consolidation: bool = False,
    fade: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_id": f"case-{index}",
        "alert_id": f"alert-{index}",
        "trade_id": f"trade-{index}",
        "case_type": "fade" if fade else "real",
        "source": "test",
        "source_id": "test-round",
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "resolved_at_utc": f"2026-01-{index + 1:02d}T00:00:00Z",
        "outcome": "WIN" if net_r > 0 else "LOSS",
        "net_r": net_r,
        "features": {
            "direction": "BUY",
            "cct": "ranging" if consolidation else "uptrend",
            "cst_v2_state": "consolidation" if consolidation else "uptrend",
            "mtf_trend_agree": not consolidation,
            "swing_reversal": False,
            "strength_score": 0.7,
            "strength_features": {
                "cst_alignment": 1 if net_r > 0 else -1,
                "cct_alignment": 1 if net_r > 0 else -1,
                "wp_agree": 1 if net_r > 0 else -1,
                "engulf": 1 if net_r > 0 else 0,
                "mtf_trend_agree": 1 if net_r > 0 else -1,
                "mtf_wp_agree": 1 if net_r > 0 else -1,
                "dual_tf_alert": 1 if net_r > 0 else 0,
            },
        },
        "formation_fingerprint": {"consecutive_lows_reversal": False},
    }


def test_formation_fingerprint_has_exact_causal_state_vector() -> None:
    states = [
        {
            "index": 0,
            "cct": "uptrend",
            "cst": "uptrend",
            "cst_v2_state": "uptrend",
            "wick_pressure": "up-pressure",
            "mtf_cst": "uptrend",
            "mtf_cct": "uptrend",
            "mtf_wick_pressure": "up-pressure",
        },
        {
            "index": 1,
            "cct": "ranging",
            "cst": "transition",
            "cst_v2_state": "downtrend",
            "wick_pressure": "balanced",
            "mtf_cst": "ranging",
            "mtf_cct": "uptrend",
            "mtf_wick_pressure": "balanced",
        },
        {
            "index": 2,
            "cct": "ranging",
            "cst": "transition",
            "cst_v2_state": "downtrend",
            "wick_pressure": "balanced",
            "mtf_cst": "downtrend",
            "mtf_cct": "downtrend",
            "mtf_wick_pressure": "down-pressure",
        },
        {
            "index": 3,
            "cct": "future-state",
            "cst": "future-state",
            "cst_v2_state": "future-state",
            "wick_pressure": "future-state",
        },
    ]
    events = [
        {
            "pivot_index": 0,
            "confirmed_index": 1,
            "type": "swing_low",
            "source": "cst_major",
            "strength_atr": 0.25,
        },
        {
            "pivot_index": 1,
            "confirmed_index": 2,
            "type": "swing_low",
            "source": "cst_major",
            "strength_atr": 1.4,
        },
        {
            "pivot_index": 2,
            "confirmed_index": 3,
            "type": "swing_high",
            "source": "cst_major",
            "strength_atr": 2.2,
        },
    ]

    fingerprint = build_formation_fingerprint(
        states, events, entry_index=3, window=3
    )

    assert fingerprint == {
        "window": 3,
        "state_count": 3,
        "axes": {
            "cct": {
                "runs": [["uptrend", 1], ["ranging", 2]],
                "transitions": {"uptrend>ranging": 1},
            },
            "cst": {
                "runs": [["uptrend", 1], ["transition", 2]],
                "transitions": {"uptrend>transition": 1},
            },
            "v2": {
                "runs": [["uptrend", 1], ["downtrend", 2]],
                "transitions": {"uptrend>downtrend": 1},
            },
            "wp": {
                "runs": [["up-pressure", 1], ["balanced", 2]],
                "transitions": {"up-pressure>balanced": 1},
            },
            "mtf_cct": {
                "runs": [["uptrend", 2], ["downtrend", 1]],
                "transitions": {"uptrend>downtrend": 1},
            },
            "mtf_cst": {
                "runs": [["uptrend", 1], ["ranging", 1], ["downtrend", 1]],
                "transitions": {
                    "ranging>downtrend": 1,
                    "uptrend>ranging": 1,
                },
            },
            "mtf_wp": {
                "runs": [["up-pressure", 1], ["balanced", 1], ["down-pressure", 1]],
                "transitions": {
                    "balanced>down-pressure": 1,
                    "up-pressure>balanced": 1,
                },
            },
        },
        "swing_sequence": [
            {
                "direction": "low",
                "source": "cst_major",
                "strength_atr_bucket": "under_0_5_atr",
            },
            {
                "direction": "low",
                "source": "cst_major",
                "strength_atr_bucket": "1_to_2_atr",
            },
        ],
        "consecutive_lows_reversal": True,
    }
    assert fingerprint["state_count"] == 3
    without_future = build_formation_fingerprint(
        states[:3], events[:2], entry_index=3, window=3
    )
    assert fingerprint == without_future


def test_case_builder_keeps_main_and_fade_without_raw_candles() -> None:
    snapshot = {
        "alert_id": "alert-x",
        "symbol": "XAUUSD",
        "tf": "M15",
        "time": "2026-01-01T00:00:00Z",
        "direction": "BUY",
        "strength_features": {"engulf": 1},
        "alert_candle_ohlc": {"open": "1"},
        "prev_candle_ohlc": {"open": "0"},
        "formation_fingerprint": {"window": 20},
    }
    trades = [
        {
            "alertId": "alert-x",
            "tradeId": "alert-x-sim",
            "symbol": "XAUUSD",
            "outcome": "WIN",
            "netR": 1.25,
            "entryTime": "2026-01-01T00:15:00Z",
            "exitTime": "2026-01-01T01:00:00Z",
        },
        {
            "alertId": "alert-x",
            "tradeId": "alert-x-fade",
            "symbol": "XAUUSD",
            "outcome": "LOSS",
            "netR": -1,
        },
    ]

    cases = cases_from_snapshot(snapshot, trades, source="replay", source_id="run-1")
    repeated = cases_from_snapshot(
        snapshot, trades, source="replay", source_id="different-run"
    )

    assert [case["case_type"] for case in cases] == ["real", "fade"]
    assert [case["case_id"] for case in cases] == [
        case["case_id"] for case in repeated
    ]
    assert all("alert_candle_ohlc" not in case["features"] for case in cases)
    assert all("prev_candle_ohlc" not in case["features"] for case in cases)
    assert all("candle" not in case["features"] for case in cases)


def test_beliefs_downweight_below_baseline_and_lessons_persist_once(
    tmp_path: Any,
) -> None:
    store = SICaseStore(
        tmp_path / "cases.jsonl",
        tmp_path / "beliefs.json",
    )
    cases = [
        _case(index, net_r=1.0, consolidation=False)
        for index in range(5)
    ] + [
        _case(index + 5, net_r=-1.0, consolidation=True)
        for index in range(5)
    ]

    assert store.ingest_cases(cases) == 10
    first = store.snapshot()
    countertrend = next(
        rule for rule in first["rules"]
        if rule["id"] == "countertrend_consolidation"
    )
    assert first["overall"]["hit_rate"] == 0.5
    assert countertrend["hit_rate"] == 0
    assert countertrend["expectancy"] == -1
    assert countertrend["flagged"] is True
    assert countertrend["weight"] == 0.1
    assert any(
        lesson["rule_id"] == "countertrend_consolidation"
        for lesson in first["lessons"]
    )
    assert store.dashboard_snapshot()["overall"]["hitRate"] == 50
    lesson_ids = [lesson["lesson_id"] for lesson in first["lessons"]]
    assert store.ingest_cases(cases) == 0

    restored = SICaseStore(
        tmp_path / "cases.jsonl",
        tmp_path / "beliefs.json",
    )
    second = restored.snapshot()
    assert second["case_count"] == 10
    assert second["overall"]["hit_rate"] == 0.5
    assert [lesson["lesson_id"] for lesson in second["lessons"]] == lesson_ids


def test_belief_hit_rate_and_expectancy_use_net_r(tmp_path: Any) -> None:
    store = SICaseStore(
        cases_path=tmp_path / "hit-rate-cases.jsonl",
        state_path=tmp_path / "hit-rate-beliefs.json",
    )
    cases = [
        _case(index, net_r=value, consolidation=True)
        for index, value in enumerate([1.0, 0.5, 2.0, -1.0, 1.0])
    ]
    store.ingest_cases(cases)
    countertrend = next(
        rule for rule in store.snapshot()["rules"]
        if rule["id"] == "countertrend_consolidation"
    )
    assert countertrend["samples"] == 5
    assert countertrend["hit_rate"] == 0.8
    assert countertrend["expectancy"] == 0.7
    assert countertrend["flagged"] is False


def test_rule_weight_decay_matches_relative_hit_rate_gap(tmp_path: Any) -> None:
    store = SICaseStore(
        cases_path=tmp_path / "decay-cases.jsonl",
        state_path=tmp_path / "decay-beliefs.json",
    )
    cases = [
        _case(index, net_r=1.0 if index == 4 or index >= 5 else -1.0, consolidation=index < 5)
        for index in range(10)
    ]

    store.ingest_cases(cases)

    countertrend = next(
        rule for rule in store.snapshot()["rules"]
        if rule["id"] == "countertrend_consolidation"
    )
    assert countertrend["hit_rate"] == 0.2
    assert countertrend["baseline_hit_rate"] == 0.6
    assert abs(countertrend["weight"] - 0.2) < 1e-9


def test_tunable_adjustments_remain_inside_approved_bounds() -> None:
    cases = [
        _case(index, net_r=1.0 if index % 2 == 0 else -1.0)
        for index in range(20)
    ]
    adjusted, tuned_at, _changed = adjust_tunables(
        cases,
        initial_tunables(),
        case_count=20,
        last_tuned_case_count=0,
    )
    validated = validate_tunables(adjusted)
    assert tuned_at == 20
    assert all(0 <= value <= 1 for value in validated["strength_weights"].values())
    assert abs(sum(validated["strength_weights"].values()) - 1) < 1e-9
    assert 0 <= validated["strength_thresholds"]["neutral_min"] <= 1
    assert 0 <= validated["strength_thresholds"]["strong_min"] <= 1
    assert (
        validated["strength_thresholds"]["neutral_min"]
        <= validated["strength_thresholds"]["strong_min"]
    )
    assert all(-1 <= value <= 1 for value in validated["invert_weights"].values())
    assert abs(sum(abs(value) for value in validated["invert_weights"].values()) - 1) < 1e-9


def test_evolution_job_passes_the_case_sink_without_live_llm_calls(
    tmp_path: Any,
) -> None:
    store = SICaseStore(
        tmp_path / "cases.jsonl",
        tmp_path / "beliefs.json",
    )
    case_seen = threading.Event()

    def fake_rounds_runner(**options: Any) -> dict[str, Any]:
        sink = options["si_case_sink"]
        sink([_case(1, net_r=1.0)])
        case_seen.set()
        return {
            "rounds": [
                {
                    "decision": "ineligible",
                    "eligible": False,
                    "result_file": "mocked-round.json",
                }
            ]
        }

    jobs = EvolveJobManager(run_rounds_fn=fake_rounds_runner)
    jobs.set_system_intelligence_sink(store.ingest_cases)
    jobs.start()

    assert case_seen.wait(timeout=5)
    assert store.snapshot()["case_count"] == 1


def test_autoevolve_round_feeds_a_case_to_system_intelligence(
    tmp_path: Any,
) -> None:
    store = SICaseStore(
        tmp_path / "autoevolve-cases.jsonl",
        tmp_path / "autoevolve-beliefs.json",
    )
    case_seen = threading.Event()
    round_finished = threading.Event()

    def fake_rounds_runner(**options: Any) -> dict[str, Any]:
        options["si_case_sink"]([_case(1, net_r=1.0)])
        case_seen.set()
        round_finished.set()
        return {
            "rounds": [{
                "decision": "ineligible",
                "eligible": False,
                "result_file": str(tmp_path / "evolve_round_01_fixture_result.json"),
            }]
        }

    jobs = EvolveJobManager(run_rounds_fn=fake_rounds_runner)
    jobs.set_system_intelligence_sink(store.ingest_cases)
    manager = AutoevolveManager(
        jobs,
        data_root=tmp_path,
        proposals_dir=tmp_path / "proposals",
        state_path=tmp_path / "autoevolve-status.json",
        active_round_path=tmp_path / "active-round.json",
        cooldown_seconds=60,
        poll_seconds=0.005,
    )

    with patch("lab.autoevolve.load_approved_market_data"):
        manager.start()
    try:
        assert case_seen.wait(timeout=5)
        assert round_finished.wait(timeout=5)
        thread = manager._thread
        assert thread is not None
        manager.stop()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert manager.status()["round"] == 1
        assert store.snapshot()["case_count"] == 1
    finally:
        if manager._thread is not None and manager._thread.is_alive():
            manager.stop()
            manager._thread.join(timeout=5)
