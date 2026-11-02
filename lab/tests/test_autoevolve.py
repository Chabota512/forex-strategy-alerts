from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from lab.autoevolve import AutoevolveManager
from lab.evolve_jobs import EvolveJobError, EvolveJobManager


def test_start_fails_clearly_when_xauusd_dataset_is_unavailable(
    tmp_path: Path,
) -> None:
    manager = AutoevolveManager(
        EvolveJobManager(run_rounds_fn=lambda **_options: {"rounds": []}),
        data_root=tmp_path,
        proposals_dir=tmp_path / "proposals",
        state_path=tmp_path / "autoevolve-status.json",
        active_round_path=tmp_path / "active-round.json",
    )

    try:
        manager.start()
    except EvolveJobError as exc:
        assert exc.status == 409
        assert "owner-approved XAUUSD dataset" in str(exc)
    else:
        raise AssertionError("AUTOEVOLVE started without an approved XAUUSD dataset.")

    assert manager.status()["running"] is False
    assert manager._thread is None


def test_stop_rejects_a_candidate_waiting_for_owner_and_stops_daemon(
    tmp_path: Path,
) -> None:
    owner_gate_replies: list[bool] = []
    owner_gate_reached = threading.Event()

    def fake_rounds_runner(**options: Any) -> dict[str, Any]:
        owner_gate_reached.set()
        approved = options["owner_gate"]({"eligible": True, "decision": "eligible"})
        owner_gate_replies.append(approved)
        result_file = tmp_path / "evolve_round_01_fixture_result.json"
        return {
            "rounds": [{
                "decision": "promoted" if approved else "not_promoted",
                "eligible": True,
                "result_file": str(result_file),
            }]
        }

    jobs = EvolveJobManager(run_rounds_fn=fake_rounds_runner)
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
    thread = manager._thread
    try:
        assert owner_gate_reached.wait(timeout=5)
        deadline = time.monotonic() + 5
        while manager.status()["state"] != "awaiting-owner-approval" and time.monotonic() < deadline:
            time.sleep(0.005)
        assert manager.status()["state"] == "awaiting-owner-approval"

        stopping_status = manager.stop()
        assert stopping_status["state"] == "stopping"
        assert stopping_status["stop_requested"] is True

        assert thread is not None
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert owner_gate_replies == [False]
        assert manager.status()["state"] == "stopped"
        assert manager.status()["stop_requested"] is False
    finally:
        if thread is not None and thread.is_alive():
            manager.stop()
            thread.join(timeout=5)
