"""Crash-safe, owner-gated continuous evolution daemon for XAUUSD."""

from __future__ import annotations

import copy
import json
import logging
import math
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import llm_client
from .candle_data import LAB_DIR
from .evolve import load_approved_market_data
from .evolve_jobs import EvolveJobError, EvolveJobManager, TERMINAL_PHASES
from .llm_client import LLMRequestError
from .versioning import atomic_write_json

logger = logging.getLogger("forex_strategy_lab.autoevolve")

COOLDOWN_SECONDS = 60
RETRY_INCREMENT_SECONDS = 10
POLL_SECONDS = 0.2
DEFAULT_STATE_PATH = LAB_DIR / "output" / "autoevolve" / "status.json"
DEFAULT_ACTIVE_ROUND_PATH = LAB_DIR / "output" / "autoevolve" / "active-round.json"
DEFAULT_PROPOSALS_DIR = LAB_DIR / "specs" / "proposals"
ROUND_STAMP_PATTERN = re.compile(r"^\d{8}T\d{12}Z$")
KEY_DEPLETION_CATEGORIES = frozenset({"auth", "quota"})
RATE_LIMIT_CATEGORIES = frozenset({"quota", "rate_limit"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _round_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _new_status(cooldown_seconds: int) -> dict[str, Any]:
    return {
        "state": "stopped",
        "running": False,
        "phase": "stopped",
        "round": 0,
        "last_verdict": None,
        "last_result_file": None,
        "active_job_id": None,
        "swing_version": "V2",
        "cooldown_seconds": cooldown_seconds,
        "cooldown_until_utc": None,
        "stop_requested": False,
        "keys": {"key1": "unknown", "key2": "unknown"},
        "alerts": [],
        "error": None,
        "updated_at_utc": _utc_now(),
    }


class AutoevolveManager:
    """Coordinates one guarded evolution round at a time and never bypasses owner approval."""

    def __init__(
        self,
        evolve_jobs: EvolveJobManager,
        *,
        data_root: Path = LAB_DIR / "data",
        proposals_dir: Path = DEFAULT_PROPOSALS_DIR,
        state_path: Path = DEFAULT_STATE_PATH,
        active_round_path: Path = DEFAULT_ACTIVE_ROUND_PATH,
        cooldown_seconds: int = COOLDOWN_SECONDS,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        if isinstance(cooldown_seconds, bool) or cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be a non-negative integer.")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive.")
        self.evolve_jobs = evolve_jobs
        self.data_root = Path(data_root)
        self.proposals_dir = Path(proposals_dir)
        self.state_path = Path(state_path)
        self.active_round_path = Path(active_round_path)
        self.cooldown_seconds = cooldown_seconds
        self.poll_seconds = poll_seconds
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = _new_status(cooldown_seconds)
        self._load_status()
        self._recover_interrupted_round()

    def _load_status(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        allowed = set(self._status)
        self._status.update({key: value for key, value in raw.items() if key in allowed})
        self._status["cooldown_seconds"] = self.cooldown_seconds
        keys = self._status.get("keys")
        if not isinstance(keys, dict):
            self._status["keys"] = {"key1": "unknown", "key2": "unknown"}
        alerts = self._status.get("alerts")
        if not isinstance(alerts, list):
            self._status["alerts"] = []

    def _persist_locked(self) -> None:
        self._status["updated_at_utc"] = _utc_now()
        atomic_write_json(self.state_path, copy.deepcopy(self._status))

    def _set_locked(self, **changes: Any) -> None:
        self._status.update(changes)
        self._persist_locked()

    def _schedule_retry(
        self,
        delay_seconds: float,
        *,
        result_file: str,
        last_verdict: str,
        error: str | None,
    ) -> float:
        delay = max(float(RETRY_INCREMENT_SECONDS), delay_seconds)
        retry_until = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + delay,
            timezone.utc,
        ).isoformat().replace("+00:00", "Z")
        with self._lock:
            self._set_locked(
                state="cooldown",
                running=True,
                phase="retry-wait",
                active_job_id=None,
                last_verdict=last_verdict,
                last_result_file=result_file,
                error=error,
                cooldown_seconds=math.ceil(delay),
                cooldown_until_utc=retry_until,
            )
        return delay

    def _archive_path(self, round_number: int, stamp: str) -> Path:
        return self.proposals_dir / f"evolve_round_{round_number:02d}_{stamp}_result.json"

    def _recover_interrupted_round(self) -> None:
        try:
            marker = json.loads(self.active_round_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            with self._lock:
                if self._status.get("running"):
                    self._set_locked(
                        state="stopped",
                        running=False,
                        phase="server-restarted",
                        active_job_id=None,
                        stop_requested=False,
                        error="The server restarted; the daemon is stopped.",
                    )
            return
        except (OSError, json.JSONDecodeError):
            marker = {}
        if not isinstance(marker, dict):
            marker = {}
        round_number = marker.get("round")
        if isinstance(round_number, bool) or not isinstance(round_number, int) or round_number < 1:
            round_number = max(1, int(self._status.get("round", 0)) + 1)
        stamp = marker.get("timestamp")
        if not isinstance(stamp, str) or not ROUND_STAMP_PATTERN.fullmatch(stamp):
            stamp = _round_stamp()
        result_path = self._archive_path(round_number, stamp)
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result = {}
        else:
            result = {
                "round": round_number,
                "timestamp_utc": marker.get("started_at_utc") or _utc_now(),
                "decision": "failed",
                "eligible": False,
                "reason": "The daemon was interrupted before this round completed.",
                "auto_evolve": True,
                "swing_version": marker.get("swing_version", "V2"),
                "provider": "groq",
                "provider_attempts": [],
                "result_file": str(result_path),
            }
            atomic_write_json(result_path, result)
        self.active_round_path.unlink(missing_ok=True)
        with self._lock:
            self._status["round"] = max(int(self._status.get("round", 0)), round_number)
            self._add_alert_locked(
                "round-interrupted",
                "An unfinished AUTOEVOLVE round was archived as failed after restart.",
                persistent=True,
            )
            self._set_locked(
                state="failed",
                running=False,
                phase="failed",
                last_verdict=str(result.get("decision") or "interrupted"),
                last_result_file=result_path.name,
                active_job_id=None,
                stop_requested=False,
                error="The server restarted during a round; a failed archive was saved.",
            )

    def _add_alert_locked(
        self,
        kind: str,
        message: str,
        *,
        persistent: bool,
        job_id: str | None = None,
    ) -> None:
        alerts = self._status.setdefault("alerts", [])
        if any(
            item.get("kind") == kind
            and item.get("job_id") == job_id
            and item.get("persistent") == persistent
            for item in alerts
            if isinstance(item, dict)
        ):
            return
        alerts.append(
            {
                "id": str(uuid.uuid4()),
                "kind": kind,
                "message": message,
                "persistent": persistent,
                "created_at_utc": _utc_now(),
                "job_id": job_id,
            }
        )
        del alerts[:-100]

    def status(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._status)

    def start(self, *, swing_version: str = "V2") -> dict[str, Any]:
        swing_version = swing_version.upper() if isinstance(swing_version, str) else ""
        if swing_version not in {"V1", "V2"}:
            raise EvolveJobError(400, "swingVersion must be V1 or V2.")
        try:
            load_approved_market_data(self.data_root, symbol="XAUUSD")
        except (OSError, ValueError) as exc:
            raise EvolveJobError(
                409,
                "AUTOEVOLVE requires an available, owner-approved XAUUSD dataset. "
                f"Load and approve the dataset before starting: {exc}",
            ) from exc
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise EvolveJobError(409, "AUTOEVOLVE is already running.")
            self._stop_event.clear()
            keys = self._status.get("keys", {})
            if self._status.get("state") == "paused-keys-depleted":
                keys = {"key1": "unknown", "key2": "unknown"}
            self._set_locked(
                state="running",
                running=True,
                phase="starting",
                active_job_id=None,
                swing_version=swing_version,
                cooldown_until_utc=None,
                stop_requested=False,
                error=None,
                keys=keys,
            )
            self._thread = threading.Thread(
                target=self._run_loop,
                name="forex-autoevolve",
                daemon=True,
            )
            self._thread.start()
            return copy.deepcopy(self._status)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._set_locked(
                    state="stopped",
                    running=False,
                    phase="stopped",
                    active_job_id=None,
                    stop_requested=False,
                    cooldown_until_utc=None,
                )
                return copy.deepcopy(self._status)
            self._stop_event.set()
            self._set_locked(
                state="stopping",
                running=True,
                stop_requested=True,
                phase="stopping-after-current-round",
            )
            return copy.deepcopy(self._status)

    def _propose_groq(self, prompt: str) -> llm_client.LLMProposalResponse:
        try:
            response = llm_client.propose(prompt, key_pool="evolve")
        except LLMRequestError as exc:
            self._record_provider_attempts(exc.attempts, failure_category=exc.category)
            raise
        self._record_provider_attempts(response.attempts)
        return response

    def _record_provider_attempts(
        self,
        attempts: list[dict[str, Any]],
        *,
        failure_category: str | None = None,
    ) -> None:
        with self._lock:
            keys = dict(self._status.get("keys") or {})
            if not attempts and failure_category == "auth":
                keys.update({"key1": "unavailable", "key2": "unavailable"})
                self._add_alert_locked(
                    "groq-keys-unavailable",
                    "No Groq credentials are configured; AUTOEVOLVE is paused.",
                    persistent=True,
                )
            for attempt in attempts:
                if attempt.get("provider") != "groq":
                    continue
                key_index = attempt.get("key_index")
                if key_index not in {1, 2}:
                    continue
                key_name = f"key{key_index}"
                category = attempt.get("category")
                if attempt.get("status") == "failed" and category in KEY_DEPLETION_CATEGORIES:
                    was_depleted = keys.get(key_name) in {"depleted", "unavailable"}
                    keys[key_name] = "depleted"
                    if key_index == 1 and not was_depleted:
                        self._add_alert_locked(
                            "groq-key1-depleted",
                            "Groq key 1 depleted — update at console.groq.com",
                            persistent=False,
                        )
                elif attempt.get("status") == "failed" and category == "rate_limit":
                    keys[key_name] = "rate-limited"
                elif attempt.get("status") == "succeeded":
                    keys[key_name] = "available"
                    for alert in self._status.get("alerts", []):
                        if (
                            isinstance(alert, dict)
                            and alert.get("kind") == "groq-keys-depleted"
                        ):
                            alert["persistent"] = False

            all_depleted = all(
                keys.get(name) in {"depleted", "unavailable"}
                for name in ("key1", "key2")
            )
            if all_depleted:
                self._add_alert_locked(
                    "groq-keys-depleted",
                    "PAUSED — both Groq keys are depleted. Update the keys, then resume AUTOEVOLVE.",
                    persistent=True,
                )
                self._status["state"] = "paused-keys-depleted"
                self._status["running"] = False
            self._set_locked(keys=keys)

    def _write_active_round(
        self, round_number: int, stamp: str, swing_version: str, **extra: Any
    ) -> dict[str, Any]:
        marker = {
            "round": round_number,
            "timestamp": stamp,
            "started_at_utc": _utc_now(),
            "swing_version": swing_version,
            **extra,
        }
        atomic_write_json(self.active_round_path, marker)
        return marker

    def _archive_failed_round(
        self, marker: dict[str, Any], message: str
    ) -> Path:
        round_number = int(marker["round"])
        stamp = str(marker["timestamp"])
        result_path = self._archive_path(round_number, stamp)
        if not result_path.exists():
            result = {
                "round": round_number,
                "timestamp_utc": marker.get("started_at_utc") or _utc_now(),
                "decision": "failed",
                "eligible": False,
                "reason": message[:500],
                "auto_evolve": True,
                "swing_version": marker.get("swing_version", "V2"),
                "provider": "groq",
                "provider_attempts": [],
                "result_file": str(result_path),
            }
            atomic_write_json(result_path, result)
        return result_path

    def _run_loop(self) -> None:
        retry_delay_seconds = 0.0
        while not self._stop_event.is_set():
            with self._lock:
                round_number = int(self._status.get("round", 0)) + 1
                swing_version = self._status["swing_version"]
            stamp = _round_stamp()
            marker = self._write_active_round(
                round_number, stamp, swing_version, phase="starting"
            )
            with self._lock:
                self._set_locked(
                    state="running",
                    running=True,
                    phase="training",
                    round=round_number,
                    active_job_id=None,
                    cooldown_until_utc=None,
                    cooldown_seconds=self.cooldown_seconds,
                    error=None,
                )
            try:
                job = self.evolve_jobs.start(
                    "XAUUSD",
                    data_root=self.data_root,
                    swing_version=swing_version,
                    request_fn=self._propose_groq,
                    round_number=round_number,
                    round_timestamp=stamp,
                )
                job_id = job["job_id"]
                marker = self._write_active_round(
                    round_number,
                    stamp,
                    swing_version,
                    phase="training",
                    job_id=job_id,
                )
                with self._lock:
                    self._set_locked(active_job_id=job_id)
                result: dict[str, Any] | None = None
                while True:
                    status = self.evolve_jobs.get_status(job_id)
                    phase = status.get("phase")
                    if phase == "awaiting-owner-decision":
                        if self._stop_event.is_set():
                            try:
                                self.evolve_jobs.decide(job_id, promote=False)
                            except EvolveJobError as exc:
                                if exc.status != 409:
                                    raise
                            with self._lock:
                                self._status["alerts"] = [
                                    alert
                                    for alert in self._status.get("alerts", [])
                                    if not (
                                        isinstance(alert, dict)
                                        and alert.get("kind") == "candidate-awaiting-approval"
                                        and alert.get("job_id") == job_id
                                    )
                                ]
                                self._set_locked(
                                    state="stopping",
                                    running=True,
                                    phase="stopping-after-current-round",
                                    active_job_id=job_id,
                                )
                        else:
                            with self._lock:
                                self._add_alert_locked(
                                    "candidate-awaiting-approval",
                                    "CANDIDATE AWAITING APPROVAL — review the existing owner gate.",
                                    persistent=True,
                                    job_id=job_id,
                                )
                                self._set_locked(
                                    state="awaiting-owner-approval",
                                    running=True,
                                    phase="awaiting-owner-decision",
                                    active_job_id=job_id,
                                )
                    elif phase not in TERMINAL_PHASES:
                        with self._lock:
                            current_state = self._status.get("state")
                            if current_state != "paused-keys-depleted":
                                self._set_locked(
                                    state="stopping"
                                    if self._stop_event.is_set()
                                    else "running",
                                    running=True,
                                    phase=phase or "training",
                                    active_job_id=job_id,
                                )
                    if phase in TERMINAL_PHASES:
                        result = status.get("result")
                        break
                    time.sleep(self.poll_seconds)

                if not isinstance(result, dict):
                    raise RuntimeError("The evolution job finished without a result.")
                result_path = result.get("result_file")
                if isinstance(result_path, str) and result_path:
                    archive_name = Path(result_path).name
                else:
                    archive_name = self._archive_failed_round(
                        marker,
                        str(status.get("error") or "The evolution job ended without an archive."),
                    ).name
                self.active_round_path.unlink(missing_ok=True)
                decision = str(result.get("decision") or "unknown")
                with self._lock:
                    paused_due_keys = (
                        self._status.get("state") == "paused-keys-depleted"
                    )
                    self._set_locked(
                        state=(
                            "paused-keys-depleted" if paused_due_keys else "running"
                        ),
                        running=not paused_due_keys,
                        phase="round-complete",
                        active_job_id=None,
                        last_verdict=decision,
                        last_result_file=archive_name,
                        error=status.get("error"),
                    )

                if self._stop_event.is_set():
                    break
                if paused_due_keys:
                    break

                request_detail = result.get("request_error_detail")
                attempts = (
                    request_detail.get("attempts", [])
                    if isinstance(request_detail, dict)
                    else result.get("provider_attempts", [])
                )
                retry_after = max(
                    (
                        float(item.get("retry_after_seconds", 0))
                        for item in attempts
                        if isinstance(item, dict)
                        and isinstance(item.get("retry_after_seconds"), (int, float))
                    ),
                    default=0.0,
                )
                categories = {
                    item.get("category")
                    for item in attempts
                    if isinstance(item, dict)
                }
                failed_round = (
                    decision in {"failed", "request_error"}
                    or status.get("phase") == "failed"
                )
                if failed_round:
                    retry_delay_seconds = max(
                        retry_delay_seconds + RETRY_INCREMENT_SECONDS,
                        retry_after if categories & RATE_LIMIT_CATEGORIES else 0.0,
                    )
                    retry_error = status.get("error") or result.get("reason")
                    delay = self._schedule_retry(
                        retry_delay_seconds,
                        result_file=archive_name,
                        last_verdict=decision,
                        error=str(retry_error) if retry_error else None,
                    )
                else:
                    retry_delay_seconds = 0.0
                    delay = max(
                        self.cooldown_seconds,
                        retry_after if categories & RATE_LIMIT_CATEGORIES else 0.0,
                    )
                    with self._lock:
                        self._set_locked(
                            state="cooldown",
                            running=True,
                            phase="cooldown",
                            cooldown_seconds=math.ceil(delay),
                            cooldown_until_utc=(
                                datetime.fromtimestamp(
                                    datetime.now(timezone.utc).timestamp() + delay,
                                    timezone.utc,
                                ).isoformat().replace("+00:00", "Z")
                                if delay > 0
                                else None
                            ),
                        )
                if self._stop_event.wait(delay):
                    break
            except EvolveJobError as exc:
                result_path = self._archive_failed_round(marker, str(exc))
                self.active_round_path.unlink(missing_ok=True)
                if self._stop_event.is_set():
                    break
                retry_delay_seconds += RETRY_INCREMENT_SECONDS
                delay = self._schedule_retry(
                    retry_delay_seconds,
                    result_file=result_path.name,
                    last_verdict="failed",
                    error=str(exc),
                )
                if self._stop_event.wait(delay):
                    break
                continue
            except Exception as exc:
                logger.exception("AUTOEVOLVE round failed")
                result_path = self._archive_failed_round(
                    marker, f"{type(exc).__name__}: the round did not complete."
                )
                self.active_round_path.unlink(missing_ok=True)
                if self._stop_event.is_set():
                    break
                retry_delay_seconds += RETRY_INCREMENT_SECONDS
                delay = self._schedule_retry(
                    retry_delay_seconds,
                    result_file=result_path.name,
                    last_verdict="failed",
                    error="The round failed; its archive was saved.",
                )
                if self._stop_event.wait(delay):
                    break
                continue

        with self._lock:
            paused = self._status.get("state") == "paused-keys-depleted"
            self._set_locked(
                state="paused-keys-depleted" if paused else "stopped",
                running=False,
                phase="paused-keys-depleted" if paused else "stopped",
                active_job_id=None,
                stop_requested=False,
                cooldown_until_utc=None,
                cooldown_seconds=self.cooldown_seconds,
            )
