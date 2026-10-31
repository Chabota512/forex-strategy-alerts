"""Background job state for the owner-gated evolution round API."""

from __future__ import annotations

import copy
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .evolve import run_rounds
from .llm_client import propose as propose_llm

logger = logging.getLogger("forex_strategy_lab.evolve.jobs")

JOB_PHASES = frozenset(
    {
        "training",
        "awaiting-proposal",
        "simulating",
        "awaiting-owner-decision",
        "complete",
        "failed",
    }
)
TERMINAL_PHASES = frozenset({"complete", "failed"})
MAX_LOG_LINES = 300


class EvolveJobError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class _Job:
    job_id: str
    symbol: str
    phase: str
    created_at: str
    updated_at: str
    logs: list[dict[str, str]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    owner_decision: bool | None = None
    decision_event: threading.Event = field(default_factory=threading.Event)


class EvolveJobManager:
    """Runs one evolution round at a time and pauses at the owner promotion gate."""

    def __init__(
        self,
        *,
        run_rounds_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._run_rounds = run_rounds_fn or run_rounds
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._active_job_id: str | None = None

    def start(
        self,
        symbol: str = "XAUUSD",
        *,
        data_root: Path | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            active = self._jobs.get(self._active_job_id or "")
            if active is not None and active.phase not in TERMINAL_PHASES:
                raise EvolveJobError(409, "An evolution round is already running.")

            now = _utc_now()
            job = _Job(
                job_id=str(uuid.uuid4()),
                symbol=symbol,
                phase="training",
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.job_id] = job
            self._active_job_id = job.job_id
            self._append_log_locked(
                job,
                "training",
                "Starting one evolution round against the approved historical dataset.",
            )
            worker = threading.Thread(
                target=self._run_job,
                args=(job, data_root),
                daemon=True,
                name=f"evolve-round-{job.job_id[:8]}",
            )
            worker.start()
            return self._status_locked(job)

    def get_status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise EvolveJobError(404, "Evolution job not found.")
            return self._status_locked(job)

    def decide(self, job_id: str, promote: bool) -> dict[str, Any]:
        if not isinstance(promote, bool):
            raise EvolveJobError(400, "promote must be a boolean.")
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise EvolveJobError(404, "Evolution job not found.")
            if (
                job.phase != "awaiting-owner-decision"
                or job.result is None
                or job.result.get("eligible") is not True
            ):
                raise EvolveJobError(
                    409, "This evolution job is not awaiting an eligible owner decision."
                )
            if job.owner_decision is not None:
                raise EvolveJobError(409, "An owner decision was already submitted.")

            job.owner_decision = promote
            self._append_log_locked(
                job,
                "simulating",
                "Owner chose to promote the candidate."
                if promote
                else "Owner chose to reject the candidate; finalizing the round.",
            )
            job.decision_event.set()
            return self._status_locked(job)

    def _run_job(self, job: _Job, data_root: Path | None = None) -> None:
        try:
            run_options: dict[str, Any] = {
                "rounds": 1,
                "symbol": job.symbol,
                "interactive": False,
                "request_fn": propose_llm,
                "owner_gate": lambda result: self._await_owner_decision(job, result),
                "phase_callback": lambda phase, message: self._publish_phase(
                    job, phase, message
                ),
            }
            if data_root is not None:
                run_options["data_root"] = data_root
            report = self._run_rounds(**run_options)
            rounds = report.get("rounds")
            if not isinstance(rounds, list) or not rounds:
                raise RuntimeError("The evolution runner returned no round result.")
            result = rounds[0]
            if not isinstance(result, dict):
                raise RuntimeError("The evolution runner returned an invalid result.")

            token_usage = result.get("token_usage")
            with self._lock:
                job.result = copy.deepcopy(result)
                if isinstance(token_usage, Mapping):
                    token_summary = ", ".join(
                        f"{key}={value}"
                        for key, value in token_usage.items()
                        if isinstance(value, (int, float))
                    )
                    if token_summary:
                        provider = result.get("provider") or "unknown"
                        key_index = result.get("key_index")
                        self._append_log_locked(
                            job,
                            "simulating",
                            f"LLM token usage provider={provider} "
                            f"key_index={key_index}: {token_summary}.",
                        )
                if result.get("decision") == "request_error":
                    detail = result.get("request_error_detail")
                    if isinstance(detail, Mapping):
                        category = detail.get("category", "unknown")
                        message = detail.get("message", "Provider request failed.")
                        job.error = f"LLM request failed ({category}): {message}"
                    else:
                        job.error = str(
                            result.get("reason") or "LLM request failed."
                        )
                    self._append_log_locked(job, "failed", job.error)
                else:
                    self._append_log_locked(
                        job,
                        "complete",
                        f"Round finished with verdict: {result.get('decision', 'unknown')}.",
                    )
        except Exception as exc:
            logger.exception("Evolution job failed job_id=%s", job.job_id)
            with self._lock:
                if job.phase not in TERMINAL_PHASES:
                    job.error = (
                        f"{type(exc).__name__}: the evolution round could not be completed."
                    )
                    self._append_log_locked(job, "failed", job.error)

    def _publish_phase(self, job: _Job, phase: str, message: str) -> None:
        with self._lock:
            if job.phase in TERMINAL_PHASES:
                return
            self._append_log_locked(job, phase, message)

    def _await_owner_decision(
        self, job: _Job, result: Mapping[str, Any]
    ) -> bool:
        if result.get("eligible") is not True:
            raise EvolveJobError(
                409, "The owner gate was requested for an ineligible candidate."
            )
        with self._lock:
            job.result = copy.deepcopy(dict(result))
            if job.phase != "awaiting-owner-decision":
                self._append_log_locked(
                    job,
                    "awaiting-owner-decision",
                    "Candidate passed the promotion gates; waiting for the owner.",
                )

        job.decision_event.wait()
        with self._lock:
            if job.owner_decision is None:
                raise EvolveJobError(409, "The owner decision was not received.")
            return job.owner_decision

    def _append_log_locked(self, job: _Job, phase: str, message: str) -> None:
        if phase not in JOB_PHASES:
            raise ValueError(f"Unknown evolution job phase: {phase}")
        now = _utc_now()
        job.phase = phase
        job.updated_at = now
        job.logs.append({"at": now, "phase": phase, "message": message})
        if len(job.logs) > MAX_LOG_LINES:
            del job.logs[: len(job.logs) - MAX_LOG_LINES]

    @staticmethod
    def _status_locked(job: _Job) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "symbol": job.symbol,
            "phase": job.phase,
            "logs": copy.deepcopy(job.logs),
            "result": copy.deepcopy(job.result),
            "error": job.error,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
