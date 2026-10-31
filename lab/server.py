"""Small standard-library HTTP API for the Forex Strategy Lab control room."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import backtest
from . import llm_client
from .alert_policy import load_policy
from .alert_snapshots import append_batch_snapshots
from .candle_data import (
    CandleFileData,
    LAB_DIR,
    load_candle_file,
    sha256_file,
)
from .monthly_data import (
    MONTHS as MONTHLY_MONTHS,
    MonthlyDataError,
    load_month,
    load_monthly_manifest,
    load_monthly_timeframe,
    monthly_file_path,
    month_sequence,
    verify_monthly_gate,
)
from .data_sanity import (
    require_owner_approved_derived_timeframe,
    require_owner_approved_sanity,
    sanity_manifest_path,
)
from .engine import body_engulfs_previous, classify_cct, compute_cst
from .evolve_jobs import EvolveJobError, EvolveJobManager
from .instruments import INSTRUMENT_REGISTRY
from .replay import (
    MAX_VISIBLE_STATES,
    ReplaySession,
    SESSION_TTL_SECONDS,
    StreamingWickPressure,
    compute_confirmed_swing_events,
)
from .shadow_detectors import compute_shadow_states
from .resample import INTERVALS, _read_m1_file
from .simulation import SimulationFilters, SimulationSettings, simulate_batch

DATA_ROOT = LAB_DIR / "data"
DATA_PATH = DATA_ROOT / "XAUUSD" / "M1"
SPEC_PATH = LAB_DIR / "specs" / "seed_v1.5.0.json"
DEFAULT_POLICY_PATH = LAB_DIR / "specs" / "policy_current.json"
OUTPUT_DIR = LAB_DIR / "output"
EVOLVE_PROPOSALS_DIR = LAB_DIR / "specs" / "proposals"
EVOLVE_RESULT_FILENAME = re.compile(
    r"^evolve_round_(\d+)_(\d{8}T\d{12}Z)_result\.json$"
)
DEFAULT_LIMIT = 100_000
MAX_LIMIT = 100_000
MAX_REPLAY_CANDLES = 500_000
MAX_REQUEST_BYTES = 64 * 1024
MAX_REPLAY_SESSIONS = 8
TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")
REVIEW_TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")
M1_REVIEW_NOTICE = (
    "M1 fires on ~1 in 4 candles — for engine checks only, not human review."
)
OWNER_ATTESTED_SANITY_METRICS = {
    "provenance": "owner_reported",
    "levelCorrelation": 0.999459,
    "hourlyChangeCorrelation": 0.962553,
    "medianFuturesOverSpotOffsetUsd": 15.895,
    "note": (
        "Owner-reported 2025 M1 comparison metrics; not independently recalculated "
        "from the local raw archive."
    ),
}
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_SYSTEM_PROMPT = (
    "You are a trading journal assistant. Given an alert (direction, timeframe, "
    "alert candle OHLC, previous candle OHLC, CCT trend, CST state, strength "
    "label), write 1–2 sentences of objective technical commentary on the "
    "context this alert sits in and anything notable. Never instruct trading, "
    "never state or imply probability of success or profitability. If context "
    "is insufficient, say what is missing. Be specific and terse."
)
TRADE_COMMENT_PROMPT_RULES = (
    "Write 2–3 sentences of objective, retrospective technical commentary. "
    "Describe the observed move and recorded outcome, then note relevant entry "
    "context such as structure, wick pressure, or MTF alignment when present. "
    "Do not give instructions, say “should have”, or state or imply probabilities, "
    "chances, odds, or future performance. Use only the supplied snapshot fields; "
    "do not infer missing details. Return JSON only with one string field named "
    "rationale."
)
TRADE_COMMENT_SNAPSHOT_FIELDS = (
    "symbol",
    "tf",
    "time",
    "direction",
    "variant",
    "strength",
    "cct",
    "cst",
    "dc_state",
    "hier_state",
    "wp_state",
    "atr14_at_alert",
    "body_ratio",
    "penetration_atr",
    "lower_wick_frac",
    "upper_wick_frac",
    "engulf",
    "mtf_cct",
    "mtf_cst",
    "mtf_wick_pressure",
    "mtf_trend_agree",
    "mtf_cct_agree",
    "mtf_wp_agree",
    "dual_tf_alert",
    "hour_utc",
    "day_of_week",
    "alert_candle_ohlc",
    "prev_candle_ohlc",
    "sim_outcome",
    "r_multiple_achieved",
    "sim_r_after_costs",
    "sim_net_pnl",
)
logger = logging.getLogger("forex_strategy_lab")


def _normalise_trade_comment(value: str) -> str | None:
    comment = " ".join(str(value or "").split()).strip()
    sentences = [
        sentence for sentence in re.split(r"(?<=[.!?])\s+", comment) if sentence
    ]
    if not 2 <= len(sentences) <= 3:
        return None
    if re.search(
        r"\b(should\s+have|probabilit(?:y|ies)|probable|likely|likelihood|"
        r"possibly|chance|odds|forecast|predict(?:ion|ed)?)\b",
        comment,
        flags=re.IGNORECASE,
    ):
        return None
    return comment


def _trade_comment_prompt(snapshot: dict[str, Any]) -> str:
    return (
        f"{TRADE_COMMENT_PROMPT_RULES}\n\n"
        "Trade snapshot (one trade only; features and context, not a raw candle series):\n"
        f"{json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=True)}"
    )


class RequestError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise RequestError(400, "Dates must use YYYY-MM-DD format.") from exc
    if parsed.isoformat() != value:
        raise RequestError(400, "Dates must use YYYY-MM-DD format.")
    return parsed


def _optional_query(params: dict[str, list[str]], name: str, default: str) -> str:
    values = params.get(name, [])
    if len(values) > 1:
        raise RequestError(400, f"{name} must be specified once.")
    return values[0] if values else default


def _json_number(value: Any) -> float:
    return float(value)


def _registered_symbol(value: Any) -> str:
    if not isinstance(value, str):
        raise RequestError(400, "A registered symbol is required.")
    symbol = value.strip().upper()
    if symbol not in INSTRUMENT_REGISTRY:
        raise RequestError(400, f"Instrument {symbol or value!r} is not registered.")
    return symbol


def _serialize_candle(
    record: dict[str, Any],
    trend_states: dict[str, Any] | None = None,
) -> dict[str, Any]:
    serialized = {
        "time": str(record["time"]),
        "open": _json_number(record["open"]),
        "high": _json_number(record["high"]),
        "low": _json_number(record["low"]),
        "close": _json_number(record["close"]),
    }
    if trend_states is not None:
        for key in ("cct", "cst", "dcState", "hierState", "wickPressure"):
            if key in trend_states:
                serialized[key] = trend_states[key]
    return serialized


def _stable_alert_id(
    source_sha256: str,
    symbol: str,
    timeframe: str,
    timestamp: str,
    direction: str,
) -> str:
    identity = {
        "input_sha256": source_sha256,
        "symbol": symbol,
        "timeframe": timeframe,
        "time": timestamp,
        "direction": direction,
    }
    payload = backtest.canonical_json(identity).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalise_comment(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return " ".join(sentences[:2]).strip()


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _history_diff_summary(proposal: Any) -> str:
    if not isinstance(proposal, dict):
        return "No proposal recorded."
    if proposal.get("no_change") is True:
        return "No strategy changes."
    diff = proposal.get("diff")
    if not isinstance(diff, list) or not diff:
        return "No structured diff recorded."

    changes: list[str] = []
    for item in diff:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        old_value = json.dumps(
            item.get("old_value", "—"), ensure_ascii=False, separators=(",", ":")
        )
        new_value = json.dumps(
            item.get("value", "—"), ensure_ascii=False, separators=(",", ":")
        )
        changes.append(f"{item['path']}: {old_value} → {new_value}")
    summary = "; ".join(changes) or "No structured diff recorded."
    return summary if len(summary) <= 180 else summary[:177] + "…"


def _evolve_round_history(proposals_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for result_path in proposals_dir.glob("evolve_round_*_result.json"):
        match = EVOLVE_RESULT_FILENAME.fullmatch(result_path.name)
        if not match or not result_path.is_file():
            continue
        archive = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(archive, dict):
            raise ValueError(f"Invalid evolution result archive: {result_path.name}")

        timestamp = archive.get("timestamp_utc")
        if not isinstance(timestamp, str) or not timestamp:
            created_at = datetime.strptime(
                match.group(2), "%Y%m%dT%H%M%S%fZ"
            ).replace(tzinfo=timezone.utc)
            timestamp = created_at.isoformat().replace("+00:00", "Z")

        candidate_validation = archive.get("candidate_validation")
        expectancy = (
            candidate_validation.get("expectancy_after_costs")
            if isinstance(candidate_validation, dict)
            else None
        )
        if (
            isinstance(expectancy, bool)
            or not isinstance(expectancy, (int, float))
            or not math.isfinite(expectancy)
        ):
            expectancy = None

        attempts = archive.get("provider_attempts")
        error_detail = archive.get("request_error_detail")
        if not isinstance(attempts, list) and isinstance(error_detail, dict):
            attempts = error_detail.get("attempts")
        attempts = attempts if isinstance(attempts, list) else []
        final_attempt = next(
            (
                item
                for item in reversed(attempts)
                if isinstance(item, dict) and item.get("status") == "succeeded"
            ),
            next((item for item in reversed(attempts) if isinstance(item, dict)), {}),
        )

        provider = archive.get("provider") or final_attempt.get("provider")
        key_index = archive.get("key_index")
        if not isinstance(key_index, int) or isinstance(key_index, bool):
            key_index = final_attempt.get("key_index")
        if not isinstance(key_index, int) or isinstance(key_index, bool):
            key_index = None

        token_usage = archive.get("token_usage")
        if not isinstance(token_usage, dict):
            token_usage = None

        round_number = archive.get("round")
        if not isinstance(round_number, int) or isinstance(round_number, bool):
            round_number = int(match.group(1))

        records.append(
            {
                "file": result_path.name,
                "timestamp_utc": timestamp,
                "round": round_number,
                "decision": (
                    archive.get("decision")
                    if isinstance(archive.get("decision"), str)
                    else "unknown"
                ),
                "eligible": archive.get("eligible") is True,
                "diff_summary": _history_diff_summary(archive.get("proposal")),
                "validation_expectancy": expectancy,
                "provider": provider if isinstance(provider, str) else None,
                "key_index": key_index,
                "token_usage": token_usage,
                "detail": _json_safe(archive),
            }
        )
    records.sort(key=lambda item: item["timestamp_utc"], reverse=True)
    return records


def _gemini_comment(
    alert: dict[str, Any],
    api_key: str,
) -> tuple[str, dict[str, int]]:
    context = {
        "direction": alert["direction"],
        "timeframe": alert["timeframe"],
        "alert_candle_ohlc": alert["candle"],
        "previous_candle_ohlc": alert["previousCandle"],
        "cct_trend": alert["cct"],
        "cst_state": alert["cst"],
        "strength_label": alert["strengthLabel"],
    }
    body = {
        "systemInstruction": {"parts": [{"text": GEMINI_SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": json.dumps(context, separators=(",", ":"))}],
            }
        ],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 120},
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Gemini request failed") from exc

    try:
        raw_comment = "".join(
            part.get("text", "")
            for part in result["candidates"][0]["content"]["parts"]
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Gemini returned no comment") from exc
    comment = _normalise_comment(raw_comment)
    if not comment:
        raise RuntimeError("Gemini returned an empty comment")

    usage_metadata = result.get("usageMetadata") or {}
    usage = {
        "promptTokens": int(usage_metadata.get("promptTokenCount", 0)),
        "responseTokens": int(usage_metadata.get("candidatesTokenCount", 0)),
        "totalTokens": int(usage_metadata.get("totalTokenCount", 0)),
    }
    return comment, usage


class LabService:
    def __init__(
        self,
        *,
        data_path: Path = DATA_PATH,
        spec_path: Path = SPEC_PATH,
        output_dir: Path = OUTPUT_DIR,
        policy_path: Path = DEFAULT_POLICY_PATH,
        ai_generator: Callable[
            [dict[str, Any], str], tuple[str, dict[str, int]]
        ]
        | None = None,
        evolve_jobs: EvolveJobManager | None = None,
    ):
        self.data_path = data_path
        self.data_dir = (
            data_path.parent.parent
            if data_path.name == "M1" and data_path.parent.name == "XAUUSD"
            else data_path.parent
        )
        self.output_dir = output_dir
        self.policy_path = policy_path
        self.evolve_jobs = evolve_jobs or EvolveJobManager()
        self.monthly_mode = (
            data_path.name == "M1" and data_path.parent.name == "XAUUSD"
        )
        self.monthly_manifest: dict[str, Any] | None = None
        self._monthly_month_cache: dict[
            tuple[str, str, str], CandleFileData
        ] = {}
        self._monthly_trend_cache: dict[
            tuple[str, str, str, str], dict[str, dict[str, Any]]
        ] = {}
        if self.monthly_mode:
            self.monthly_manifest = load_monthly_manifest(self.data_dir)
            self.source = str(self.monthly_manifest["source"])
            self.source_sha256 = verify_monthly_gate(
                self.data_dir,
                manifest=self.monthly_manifest,
            )
            rows: list[dict[str, Any]] = []
        else:
            if data_path.is_dir():
                raise ValueError(
                    "XAUUSD data must use the verified monthly M1 directory."
                )
            symbol, _has_source, rows = _read_m1_file(data_path)
            if symbol != "XAUUSD":
                raise ValueError(f"Expected XAUUSD candles, got {symbol}.")
            source_values = {
                str(row["source"]).strip().lower()
                for row in rows
                if str(row.get("source", "")).strip()
            }
            if source_values != {"dukascopy"}:
                raise ValueError(
                    "The control room requires the approved Dukascopy XAUUSD source."
                )
            if len(source_values) > 1:
                raise ValueError("M1 source labels must be consistent.")
            self.source = "dukascopy"
            self.source_sha256 = sha256_file(data_path)
        self.spec = backtest.load_spec(spec_path)
        self.ai_generator = ai_generator or _gemini_comment
        self.cache_lock = threading.RLock()
        self._data_lock = threading.RLock()
        self._trend_state_cache: dict[
            tuple[str, str], tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]
        ] = {}
        self._swing_event_cache: dict[
            tuple[str, str, str, str, str], list[dict[str, Any]]
        ] = {}
        self._m1_index_cache: tuple[
            Any, list[str], dict[str, tuple[int, int]]
        ] | None = None
        self._replay_sessions: dict[str, ReplaySession] = {}
        self._replay_sessions_lock = threading.RLock()

        self.data_by_timeframe = (
            {}
            if self.monthly_mode
            else self._build_timeframes(
                data_path, rows, self.source, self.source_sha256
            )
        )
        self.data_by_symbol: dict[str, dict[str, CandleFileData]] = (
            {} if self.monthly_mode else {"XAUUSD": self.data_by_timeframe}
        )

    @staticmethod
    def _build_timeframes(
        data_path: Path,
        rows: list[dict[str, Any]],
        source: str,
        source_sha256: str,
    ) -> dict[str, CandleFileData]:
        data_by_timeframe: dict[str, CandleFileData] = {
            "M1": CandleFileData(
                candles=rows,
                path=data_path,
                source=source,
                sha256=source_sha256,
            )
        }
        symbol = data_path.stem.removesuffix("_M1").upper()
        for timeframe in INTERVALS:
            try:
                approval = require_owner_approved_derived_timeframe(
                    data_path.parent,
                    symbol=symbol,
                    timeframe=timeframe,
                    m1_sha256=source_sha256,
                )
                derived = load_candle_file(approval["path"])
            except (OSError, ValueError):
                continue
            data_by_timeframe[timeframe] = CandleFileData(
                candles=derived.candles,
                path=derived.path,
                source=source,
                sha256=source_sha256,
            )
        return data_by_timeframe

    def _load_symbol_data(self, symbol: str) -> dict[str, CandleFileData]:
        symbol = _registered_symbol(symbol)
        with self._data_lock:
            cached = self.data_by_symbol.get(symbol)
            if cached is not None:
                return cached

            if self.monthly_mode and symbol == "XAUUSD":
                if self.monthly_manifest is None:
                    raise RequestError(409, "The verified XAUUSD monthly manifest is unavailable.")
                loaded = {
                    timeframe: load_monthly_timeframe(
                        self.data_dir,
                        symbol,
                        timeframe,
                        manifest=self.monthly_manifest,
                    )
                    for timeframe in TIMEFRAMES
                }
                self.data_by_symbol[symbol] = loaded
                return loaded

            if symbol == "XAUUSD":
                raise RequestError(
                    409,
                    "XAUUSD candles must be loaded from the verified monthly dataset.",
                )
            data_path = self.data_dir / f"{symbol}_M1.csv"
            if not data_path.is_file():
                raise RequestError(404, f"No local candle data is available for {symbol}.")
            try:
                file_symbol, _has_source, rows = _read_m1_file(data_path)
            except (OSError, ValueError) as exc:
                raise RequestError(400, f"Could not load {symbol} candle data: {exc}") from exc
            if file_symbol != symbol:
                raise RequestError(400, f"Expected {symbol}_M1.csv, got {file_symbol}_M1.csv.")
            source_values = {
                str(row.get("source", "")).strip().lower()
                for row in rows
                if str(row.get("source", "")).strip()
            }
            if source_values != {"dukascopy"}:
                raise RequestError(409, f"{symbol} requires the approved Dukascopy source.")
            source_sha256 = sha256_file(data_path)
            loaded = self._build_timeframes(
                data_path, rows, "dukascopy", source_sha256
            )
            self.data_by_symbol[symbol] = loaded
            return loaded

    def _load_monthly_month(
        self, symbol: str, timeframe: str, month: str
    ) -> CandleFileData:
        symbol = _registered_symbol(symbol)
        timeframe = timeframe.upper()
        if not self.monthly_mode or symbol != "XAUUSD" or self.monthly_manifest is None:
            raise RequestError(404, f"No monthly candle data is available for {symbol}.")
        key = (symbol, timeframe, month)
        cached = self._monthly_month_cache.get(key)
        if cached is not None:
            return cached
        try:
            data = load_month(
                self.data_dir,
                symbol,
                timeframe,
                month,
                manifest=self.monthly_manifest,
            )
        except (OSError, ValueError) as exc:
            raise RequestError(409, f"Could not load verified {symbol} {timeframe} {month}: {exc}") from exc
        if len(self._monthly_month_cache) >= 8:
            self._monthly_month_cache.pop(next(iter(self._monthly_month_cache)))
        self._monthly_month_cache[key] = data
        return data

    def _data_for(self, symbol: str, timeframe: str) -> CandleFileData:
        symbol_data = self._load_symbol_data(symbol)
        try:
            return symbol_data[timeframe]
        except KeyError as exc:
            raise RequestError(
                409,
                f"{symbol} {timeframe} data is missing, stale, or not approved.",
            ) from exc

    def _trend_states_for_timeframe(
        self, timeframe: str, symbol: str = "XAUUSD"
    ) -> dict[str, dict[str, Any]]:
        symbol = _registered_symbol(symbol)
        source_data = self._data_for(symbol, timeframe)
        cache_key = (symbol, timeframe)
        cached = self._trend_state_cache.get(cache_key)
        if cached is not None and cached[0] is source_data.candles:
            return cached[1]

        candles = backtest.candle_objects(source_data.candles)
        cct_settings = self.spec["cct"]
        cst_settings = self.spec["cst"]
        cct_window = int(cct_settings["window"])
        cct_neighbors = int(cct_settings["swing_neighbors"])
        cct_states = [
            classify_cct(
                candles[max(0, index + 1 - cct_window) : index + 1],
                window=cct_window,
                swing_neighbors=cct_neighbors,
            )
            for index in range(len(candles))
        ]
        cst_states = compute_cst(
            candles,
            swing_neighbors=int(cst_settings["major_swing_neighbors"]),
            break_basis=cst_settings["break_basis"],
        )
        shadow_states = compute_shadow_states(candles)
        states = {
            str(record["time"]): {
                "cct": str(cct_states[index]),
                "cst": str(cst_states[index]),
                "dcState": str(shadow_states[index]["dc_state"]),
                "hierState": str(shadow_states[index]["hier_state"]),
                "hierPromotions": shadow_states[index]["hier_promotions"],
            }
            for index, record in enumerate(source_data.candles)
        }
        self._trend_state_cache[cache_key] = (source_data.candles, states)
        return states

    def _trend_states_for_month(
        self,
        timeframe: str,
        symbol: str,
        month: str,
        source_data: CandleFileData,
    ) -> dict[str, dict[str, Any]]:
        cache_key = (symbol, timeframe, month, source_data.sha256)
        cached = self._monthly_trend_cache.get(cache_key)
        if cached is not None:
            return cached
        candles = backtest.candle_objects(source_data.candles)
        cct_settings = self.spec["cct"]
        cst_settings = self.spec["cst"]
        cct_window = int(cct_settings["window"])
        cct_neighbors = int(cct_settings["swing_neighbors"])
        cct_states = [
            classify_cct(
                candles[max(0, index + 1 - cct_window) : index + 1],
                window=cct_window,
                swing_neighbors=cct_neighbors,
            )
            for index in range(len(candles))
        ]
        cst_states = compute_cst(
            candles,
            swing_neighbors=int(cst_settings["major_swing_neighbors"]),
            break_basis=cst_settings["break_basis"],
        )
        shadow_states = compute_shadow_states(candles)
        states = {
            str(record["time"]): {
                "cct": str(cct_states[index]),
                "cst": str(cst_states[index]),
                "dcState": str(shadow_states[index]["dc_state"]),
                "hierState": str(shadow_states[index]["hier_state"]),
                "hierPromotions": shadow_states[index]["hier_promotions"],
            }
            for index, record in enumerate(source_data.candles)
        }
        if len(self._monthly_trend_cache) >= 12:
            self._monthly_trend_cache.pop(next(iter(self._monthly_trend_cache)))
        self._monthly_trend_cache[cache_key] = states
        return states

    def _swing_events_for_window(
        self,
        *,
        symbol: str,
        timeframe: str,
        from_value: str,
        to_value: str,
        source_sha256: str,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        cache_key = (symbol, timeframe, from_value, to_value, source_sha256)
        cached = self._swing_event_cache.get(cache_key)
        if cached is not None:
            return cached
        events = compute_confirmed_swing_events(records, self.spec)
        if len(self._swing_event_cache) >= 12:
            self._swing_event_cache.pop(next(iter(self._swing_event_cache)))
        self._swing_event_cache[cache_key] = events
        return events

    def dataset_info(self, symbol: str = "XAUUSD") -> dict[str, Any]:
        symbol = _registered_symbol(symbol)
        if self.monthly_mode and symbol == "XAUUSD":
            if self.monthly_manifest is None:
                raise RequestError(409, "The verified XAUUSD monthly manifest is unavailable.")
            monthly_m1 = self.monthly_manifest["timeframes"]["M1"]
            instrument_reviews = self._instrument_review_info()
            active_review = next(
                review for review in instrument_reviews if review["symbol"] == symbol
            )
            return {
                "symbol": symbol,
                "source": self.source,
                "sourceSha256": self.source_sha256,
                "startDate": "2023-01-01",
                "endDate": "2025-12-31",
                "candleCount": sum(
                    monthly_m1[month]["row_count"] for month in MONTHLY_MONTHS
                ),
                "reviewStatus": active_review["status"],
                "ownerApproved": active_review["ownerApproved"],
                "scoringEnabled": active_review["ownerApproved"],
                "simulationCost": float(INSTRUMENT_REGISTRY[symbol].simulation_cost),
                "instrumentReviews": instrument_reviews,
                "availableTimeframes": list(TIMEFRAMES),
                "defaultReviewTimeframes": list(REVIEW_TIMEFRAMES),
                "m1ReviewNotice": M1_REVIEW_NOTICE,
                "ownerAttestedSanityMetrics": dict(OWNER_ATTESTED_SANITY_METRICS),
            }
        data_by_timeframe = self._load_symbol_data(symbol)
        m1 = data_by_timeframe["M1"].candles
        if not m1:
            raise RequestError(409, f"The local {symbol} dataset is empty.")
        instrument_reviews = self._instrument_review_info()
        active_review = next(
            review for review in instrument_reviews if review["symbol"] == symbol
        )
        source_data = data_by_timeframe["M1"]
        return {
            "symbol": symbol,
            "source": source_data.source,
            "sourceSha256": source_data.sha256,
            "startDate": (
                f"{min(active_review['requiredYears']):04d}-01-01"
                if active_review["requiredYears"]
                else str(m1[0]["time"])[:10]
            ),
            "endDate": (
                f"{max(active_review['requiredYears']):04d}-12-31"
                if active_review["requiredYears"]
                else str(m1[-1]["time"])[:10]
            ),
            "candleCount": len(m1),
            "reviewStatus": active_review["status"],
            "ownerApproved": active_review["ownerApproved"],
            "scoringEnabled": active_review["ownerApproved"],
            "simulationCost": float(INSTRUMENT_REGISTRY[symbol].simulation_cost),
            "instrumentReviews": instrument_reviews,
            "availableTimeframes": [
                timeframe for timeframe in TIMEFRAMES if timeframe in data_by_timeframe
            ],
            "defaultReviewTimeframes": [
                timeframe
                for timeframe in REVIEW_TIMEFRAMES
                if timeframe in data_by_timeframe
            ],
            "m1ReviewNotice": M1_REVIEW_NOTICE,
            "ownerAttestedSanityMetrics": dict(OWNER_ATTESTED_SANITY_METRICS),
        }

    def current_policy(self) -> dict[str, Any]:
        return load_policy(self.policy_path)

    def _instrument_review_info(self) -> list[dict[str, Any]]:
        data_dir = self.data_dir
        reviews: list[dict[str, Any]] = []
        for symbol in INSTRUMENT_REGISTRY:
            if self.monthly_mode and symbol == "XAUUSD" and self.monthly_manifest:
                m1_records = self.monthly_manifest["timeframes"]["M1"]
                first_data = self._load_monthly_month(symbol, "M1", MONTHLY_MONTHS[0])
                last_data = self._load_monthly_month(symbol, "M1", MONTHLY_MONTHS[-1])
                row_count = sum(
                    m1_records[month]["row_count"] for month in MONTHLY_MONTHS
                )
                reviews.append(
                    {
                        "symbol": symbol,
                        "status": "OWNER_APPROVED",
                        "ownerApproved": True,
                        "dataAvailable": True,
                        "dataComplete": True,
                        "requiredYears": [2023, 2024, 2025],
                        "availableYears": [2023, 2024, 2025],
                        "rowCount": row_count,
                        "startDate": str(first_data.candles[0]["time"])[:10],
                        "endDate": str(last_data.candles[-1]["time"])[:10],
                    }
                )
                continue
            canonical_path = (
                self.data_path
                if symbol == "XAUUSD"
                else data_dir / f"{symbol}_M1.csv"
            )
            manifest_path = sanity_manifest_path(data_dir, symbol, "M1")
            manifest: dict[str, Any] = {}
            manifest_exists = manifest_path.is_file()
            if manifest_exists:
                try:
                    parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        manifest = parsed
                except (OSError, json.JSONDecodeError):
                    manifest = {}

            data_available = canonical_path.is_file()
            current_hash: str | None = None
            if data_available:
                if canonical_path.resolve() == self.data_path.resolve():
                    current_hash = self.source_sha256
                else:
                    stat = canonical_path.stat()
                    cache = getattr(self, "_instrument_hash_cache", {})
                    cached = cache.get(symbol)
                    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                        current_hash = cached[2]
                    else:
                        current_hash = sha256_file(canonical_path)
                        cache[symbol] = (stat.st_mtime_ns, stat.st_size, current_hash)
                        self._instrument_hash_cache = cache
            approved = False
            if not data_available:
                status = "MISSING_DATA"
            elif not manifest_exists:
                status = "REVIEW_REQUIRED"
            elif not manifest:
                status = "INVALID_MANIFEST"
            elif manifest.get("primary_sha256") != current_hash:
                status = "STALE_MANIFEST"
            else:
                status = str(manifest.get("status") or "REVIEW_REQUIRED")
                try:
                    require_owner_approved_sanity(
                        data_dir,
                        symbol=symbol,
                        timeframe="M1",
                        primary_sha256=str(current_hash),
                    )
                except ValueError:
                    pass
                else:
                    status = "OWNER_APPROVED"
                    approved = True

            integrity = manifest.get("local_integrity")
            if not isinstance(integrity, dict):
                integrity = {}
            required_years = manifest.get("required_years")
            available_years = manifest.get("available_source_years")
            reviews.append(
                {
                    "symbol": symbol,
                    "status": status,
                    "ownerApproved": approved,
                    "dataAvailable": data_available,
                    "dataComplete": manifest.get("data_complete") is True,
                    "requiredYears": (
                        required_years if isinstance(required_years, list) else []
                    ),
                    "availableYears": (
                        available_years if isinstance(available_years, list) else []
                    ),
                    "rowCount": int(integrity.get("row_count") or 0),
                    "startDate": (
                        str(integrity["start_utc"])[:10]
                        if integrity.get("start_utc")
                        else None
                    ),
                    "endDate": (
                        str(integrity["end_utc"])[:10]
                        if integrity.get("end_utc")
                        else None
                    ),
                }
            )
        return reviews

    def _require_approved_market_data(self, symbol: str = "XAUUSD") -> None:
        symbol = _registered_symbol(symbol)
        if self.monthly_mode and symbol == "XAUUSD":
            if (
                self.monthly_manifest is None
                or not self.source_sha256
            ):
                raise RequestError(409, "The XAUUSD raw-data fidelity gate is not verified.")
            return
        source_data = self._data_for(symbol, "M1")
        try:
            require_owner_approved_sanity(
                source_data.path.parent,
                symbol=symbol,
                timeframe="M1",
                primary_sha256=source_data.sha256,
            )
        except ValueError as exc:
            raise RequestError(409, str(exc)) from exc

    def _prune_replay_sessions(self) -> None:
        now = time.monotonic()
        expired = [
            session_id
            for session_id, session in self._replay_sessions.items()
            if now - session.last_access > SESSION_TTL_SECONDS
        ]
        for session_id in expired:
            self._replay_sessions[session_id].flush_snapshots()
            del self._replay_sessions[session_id]
        while len(self._replay_sessions) >= MAX_REPLAY_SESSIONS:
            oldest = min(
                self._replay_sessions,
                key=lambda session_id: self._replay_sessions[session_id].last_access,
            )
            self._replay_sessions[oldest].flush_snapshots()
            del self._replay_sessions[oldest]

    def create_replay_session(
        self,
        timeframe: str,
        from_value: str,
        to_value: str,
        *,
        symbol: str = "XAUUSD",
        variant_b: bool = False,
        simulation_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        symbol = _registered_symbol(symbol)
        timeframe = timeframe.upper()
        if timeframe not in TIMEFRAMES:
            raise RequestError(400, f"Timeframe must be one of {', '.join(TIMEFRAMES)}.")
        from_date = _parse_date(from_value)
        to_date = _parse_date(to_value)
        if from_date > to_date:
            raise RequestError(400, "The start date must be on or before the end date.")

        self._require_approved_market_data(symbol)
        mtf_records = None
        try:
            if self.monthly_mode and symbol == "XAUUSD":
                start_month = from_date.strftime("%Y-%m")
                end_month = to_date.strftime("%Y-%m")
                monthly_window = load_monthly_timeframe(
                    self.data_dir,
                    symbol,
                    timeframe,
                    start_month=start_month,
                    end_month=end_month,
                    manifest=self.monthly_manifest,
                )
                source_data = monthly_window
                records = backtest.filter_candles_by_date_window(
                    source_data.candles, from_date, to_date
                )
                if timeframe == "M5":
                    mtf_records = load_monthly_timeframe(
                        self.data_dir,
                        symbol,
                        "M15",
                        start_month=start_month,
                        end_month=end_month,
                        manifest=self.monthly_manifest,
                    ).candles
            else:
                symbol_data = self._load_symbol_data(symbol)
                source_data = symbol_data[timeframe]
                mtf_records = (
                    symbol_data["M15"].candles if timeframe == "M5" else None
                )
                if timeframe == "M1":
                    times, _ = self._m1_month_indexes(source_data.candles)
                    start = bisect_left(times, from_date.isoformat())
                    end = bisect_right(times, to_date.isoformat() + "\uffff")
                    records = source_data.candles[start:end]
                    if not records:
                        backtest.filter_candles_by_date_window(
                            source_data.candles, from_date, to_date
                        )
                else:
                    records = backtest.filter_candles_by_date_window(
                        source_data.candles, from_date, to_date
                    )
        except ValueError as exc:
            raise RequestError(400, str(exc)) from exc
        if len(records) > MAX_REPLAY_CANDLES:
            raise RequestError(
                413,
                f"This window contains more than {MAX_REPLAY_CANDLES:,} candles. Choose a shorter month range.",
            )

        replay_spec = {
            **self.spec,
            "instruments": [symbol],
            "timeframes": [timeframe],
        }
        settings_fingerprint = backtest.settings_fingerprint(replay_spec)
        run_id = backtest._run_id(
            settings_fingerprint, {(symbol, timeframe): source_data}
        )
        session = ReplaySession(
            session_id=uuid.uuid4().hex,
            run_id=run_id,
            timeframe=timeframe,
            from_value=from_value,
            to_value=to_value,
            records=records,
            source=source_data.source,
            source_sha256=source_data.sha256,
            spec=replay_spec,
            settings_fingerprint=settings_fingerprint,
            variant_b=variant_b,
            simulation_settings=simulation_settings,
            alert_policy=self.current_policy(),
            mtf_records=mtf_records,
            snapshot_path=self.output_dir / "snapshots.jsonl",
        )
        with self._replay_sessions_lock:
            self._prune_replay_sessions()
            self._replay_sessions[session.session_id] = session
        response = session.initial_response()
        response["symbol"] = symbol
        state = response["states"][0]
        response["currentCandle"] = _serialize_candle(
            records[0],
            {
                "cct": state["cct"],
                "cst": state["cst"],
                "dcState": state["dcState"],
                "hierState": state["hierState"],
                "wickPressure": response["wickPressureStates"][0]["state"],
                "mtfCst": state["mtfCst"],
                "mtfCct": state["mtfCct"],
                "mtfWickPressure": state["mtfWickPressure"],
                "mtfClosedAt": state["mtfClosedAt"],
            },
        )
        return response

    def advance_replay_session(
        self,
        session_id: str,
        *,
        to_index: int,
        view_start: int,
        view_end: int,
        simulation_filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._replay_sessions_lock:
            session = self._replay_sessions.get(session_id)
        if session is None:
            raise RequestError(404, "Replay session was not found or has expired.")
        try:
            active_filters = SimulationFilters.from_payload(simulation_filters)
            response = session.advance(
                to_index=to_index,
                view_start=view_start,
                view_end=view_end,
                simulation_filters=active_filters.to_dict(),
            )
        except ValueError as exc:
            raise RequestError(400, str(exc)) from exc

        response["newAlerts"] = [
            {
                "candleIndex": entry["candleIndex"],
                "alert": {
                    "alertId": entry["alert"]["alert_id"],
                    "time": entry["alert"]["time"],
                    "symbol": entry["alert"]["symbol"],
                    "timeframe": entry["alert"]["tf"],
                    "direction": entry["alert"]["direction"],
                    "alertType": entry["alert"]["alert_type"],
                    "engulfing": body_engulfs_previous(
                        session._to_candle(
                            entry["alert"]["previous_candle"]
                        ),
                        session._to_candle(entry["alert"]["candle"]),
                        entry["alert"]["direction"],
                    ),
                    "cct": entry["alert"]["cct"],
                    "cst": entry["alert"]["cst"],
                    "mtfCst": entry["alert"]["mtf_cst"],
                    "mtfCct": entry["alert"]["mtf_cct"],
                    "mtfWickPressure": entry["alert"]["mtf_wick_pressure"],
                    "mtfClosedAt": entry["alert"]["mtf_closed_at"],
                    "mtfTrendAgree": entry["alert"]["mtf_trend_agree"],
                    "mtfCctAgree": entry["alert"]["mtf_cct_agree"],
                    "mtfWpAgree": entry["alert"]["mtf_wp_agree"],
                    "dualTfAlert": entry["alert"]["dual_tf_alert"],
                    "dcState": entry["alert"]["dc_state"],
                    "hierState": entry["alert"]["hier_state"],
                    "strengthLabel": entry["alert"]["strength_label"],
                    "note": entry["alert"]["note"] or None,
                    "candle": _serialize_candle(entry["alert"]["candle"]),
                    "previousCandle": _serialize_candle(
                        entry["alert"]["previous_candle"]
                    ),
                    "scored": False,
                },
            }
            for entry in response["newAlerts"]
        ]
        current_record = session.records[to_index]
        current_state = (
            session.states[to_index]
            if to_index <= session.computed_through
            else None
        )
        response["currentCandle"] = _serialize_candle(
            current_record,
            {
                "cct": current_state["cct"],
                "cst": current_state["cst"],
                "dcState": current_state["dcState"],
                "hierState": current_state["hierState"],
                "hierPromotions": current_state["hierPromotions"],
                "wickPressure": session.wick_pressure_states[to_index],
                "mtfCst": current_state["mtfCst"],
                "mtfCct": current_state["mtfCct"],
                "mtfWickPressure": current_state["mtfWickPressure"],
                "mtfClosedAt": current_state["mtfClosedAt"],
            }
            if current_state
            else None,
        )
        response["expiresInSeconds"] = SESSION_TTL_SECONDS
        return response

    def _m1_month_indexes(
        self, candles: list[dict[str, Any]]
    ) -> tuple[list[str], dict[str, tuple[int, int]]]:
        cached = self._m1_index_cache
        if cached is not None and cached[0] is candles:
            return cached[1], cached[2]

        times = [str(candle["time"]) for candle in candles]
        month_ranges: dict[str, tuple[int, int]] = {}
        if times:
            month_start = 0
            current_month = times[0][:7]
            for index, timestamp in enumerate(times[1:], start=1):
                month = timestamp[:7]
                if month != current_month:
                    month_ranges[current_month] = (month_start, index)
                    month_start = index
                    current_month = month
            month_ranges[current_month] = (month_start, len(times))
        self._m1_index_cache = (candles, times, month_ranges)
        return times, month_ranges

    def candle_window(
        self,
        timeframe: str,
        from_value: str,
        to_value: str,
        limit: int = DEFAULT_LIMIT,
        chunk_month: str | None = None,
        symbol: str = "XAUUSD",
    ) -> dict[str, Any]:
        symbol = _registered_symbol(symbol)
        timeframe = timeframe.upper()
        if timeframe not in TIMEFRAMES:
            raise RequestError(400, "Timeframe must be M1, M5, or M15.")
        if limit < 1 or limit > MAX_LIMIT:
            raise RequestError(400, f"Limit must be between 1 and {MAX_LIMIT}.")
        from_date = _parse_date(from_value)
        to_date = _parse_date(to_value)
        if from_date > to_date:
            raise RequestError(400, "The start date must be on or before the end date.")
        if self.monthly_mode and symbol == "XAUUSD":
            if self.monthly_manifest is None:
                raise RequestError(409, "The verified XAUUSD monthly manifest is unavailable.")
            try:
                months = month_sequence(
                    from_date.strftime("%Y-%m"), to_date.strftime("%Y-%m")
                )
            except ValueError as exc:
                raise RequestError(400, str(exc)) from exc
            available_months = set(self.monthly_manifest["months"])
            if any(month not in available_months for month in months):
                raise RequestError(400, "The selected month range is outside the available data.")

            month_records: dict[str, list[dict[str, Any]]] = {}
            chunks: list[dict[str, Any]] = []
            start_index = 0
            for month in months:
                record = self.monthly_manifest["timeframes"][timeframe][month]
                month_start = date.fromisoformat(f"{month}-01")
                next_month_start = (
                    date(month_start.year + 1, 1, 1)
                    if month_start.month == 12
                    else date(month_start.year, month_start.month + 1, 1)
                )
                full_month = from_date <= month_start and to_date >= (
                    next_month_start - timedelta(days=1)
                )
                count = int(record["row_count"])
                if not full_month:
                    edge_data = self._load_monthly_month(symbol, timeframe, month)
                    filtered = [
                        candle
                        for candle in edge_data.candles
                        if from_date.isoformat() <= str(candle["time"])[:10] <= to_date.isoformat()
                    ]
                    month_records[month] = filtered
                    count = len(filtered)
                if count:
                    chunks.append(
                        {"month": month, "startIndex": start_index, "count": count}
                    )
                    start_index += count
            if not chunks:
                raise RequestError(400, "No candles found in the selected month range.")
            if chunk_month is not None:
                try:
                    month_sequence(chunk_month, chunk_month)
                except ValueError as exc:
                    raise RequestError(400, str(exc)) from exc
            response_month = chunk_month or chunks[0]["month"]
            selected_chunk = next(
                (item for item in chunks if item["month"] == response_month), None
            )
            if selected_chunk is None:
                raise RequestError(400, "Chunk month is outside the selected date window.")
            data = self._load_monthly_month(symbol, timeframe, response_month)
            selected_records = month_records.get(response_month)
            if selected_records is None:
                selected_records = data.candles
            selected_records = selected_records[:limit]
            if not selected_records:
                raise RequestError(400, "No candles found in the selected month.")
            trend_states = (
                {}
                if timeframe == "M1"
                else self._trend_states_for_month(
                    timeframe, symbol, response_month, data
                )
            )
            swing_events = self._swing_events_for_window(
                symbol=symbol,
                timeframe=timeframe,
                from_value=f"{response_month}-01",
                to_value=f"{response_month}-31",
                source_sha256=data.sha256,
                records=data.candles,
            )
            offset = int(selected_chunk["startIndex"])
            shifted_swing_events = [
                {
                    **event,
                    "pivot_index": int(event["pivot_index"]) + offset,
                    "confirmed_index": int(event["confirmed_index"]) + offset,
                }
                for event in swing_events
            ]
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "from": from_value,
                "to": to_value,
                "candles": [
                    _serialize_candle(
                        candle,
                        trend_states.get(str(candle["time"])),
                    )
                    for candle in selected_records
                ],
                "swingEvents": shifted_swing_events,
                "totalCandles": start_index,
                "limit": limit,
                "truncated": int(selected_chunk["count"]) > limit,
                "chunks": chunks,
                "chunkMonth": response_month,
                "chunkOffset": offset,
                "source": self.source,
                "sourceSha256": self.source_sha256,
            }
        source_data = self._data_for(symbol, timeframe)
        chunks: list[dict[str, Any]] = []
        response_chunk_month: str | None = None
        chunk_offset = 0
        if timeframe == "M1":
            times, month_ranges = self._m1_month_indexes(source_data.candles)
            window_start = bisect_left(times, from_date.isoformat())
            window_end = bisect_right(times, to_date.isoformat() + "\uffff")
            if window_start == window_end:
                try:
                    backtest.filter_candles_by_date_window(
                        source_data.candles, from_date, to_date
                    )
                except ValueError as exc:
                    raise RequestError(400, str(exc)) from exc
                raise RequestError(400, "No candles found in the selected date window.")
            swing_window_records = source_data.candles[window_start:window_end]

            for month, (month_start, month_end) in month_ranges.items():
                start = max(window_start, month_start)
                end = min(window_end, month_end)
                if start < end:
                    chunks.append(
                        {
                            "month": month,
                            "startIndex": start - window_start,
                            "count": end - start,
                        }
                    )
            if chunk_month is not None and not re.fullmatch(
                r"\d{4}-(0[1-9]|1[0-2])", chunk_month
            ):
                raise RequestError(400, "Chunk month must use YYYY-MM format.")
            response_chunk_month = chunk_month or chunks[-1]["month"]
            chunk = next(
                (item for item in chunks if item["month"] == response_chunk_month),
                None,
            )
            if chunk is None:
                raise RequestError(
                    400, "Chunk month is outside the selected date window."
                )
            chunk_offset = int(chunk["startIndex"])
            chunk_start = window_start + chunk_offset
            chunk_count = int(chunk["count"])
            chunk_end = chunk_start + min(chunk_count, limit)
            context_start = max(window_start, chunk_start - 99)
            selected = source_data.candles[context_start:chunk_end]
            chunk_offset = context_start - window_start
            total_candles = window_end - window_start
            truncated = chunk_count > limit
        else:
            if chunk_month is not None:
                raise RequestError(400, "Month chunks are only supported for M1.")
            try:
                selected = backtest.filter_candles_by_date_window(
                    source_data.candles, from_date, to_date
                )
            except ValueError as exc:
                raise RequestError(400, str(exc)) from exc
            swing_window_records = selected
            total_candles = len(selected)
            truncated = total_candles > limit

        visible_records = selected if timeframe == "M1" else selected[:limit]
        # M1 windows are served month-by-month; defer its full-year trend pass
        # until an incremental replay actually requests engine states.
        trend_states = {} if timeframe == "M1" else self._trend_states_for_timeframe(
            timeframe, symbol
        )
        swing_events = self._swing_events_for_window(
            symbol=symbol,
            timeframe=timeframe,
            from_value=from_value,
            to_value=to_value,
            source_sha256=source_data.sha256,
            records=swing_window_records,
        )
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "from": from_value,
            "to": to_value,
            "candles": [
                _serialize_candle(candle, trend_states.get(str(candle["time"])))
                for candle in visible_records
            ],
            "swingEvents": swing_events,
            "totalCandles": total_candles,
            "limit": limit,
            "truncated": truncated,
            "chunks": chunks,
            "chunkMonth": response_chunk_month,
            "chunkOffset": chunk_offset,
            "source": source_data.source,
            "sourceSha256": source_data.sha256,
        }

    def backtest_window(
        self,
        timeframe: str,
        from_value: str,
        to_value: str,
        *,
        symbol: str = "XAUUSD",
        simulation_settings: dict[str, Any] | None = None,
        simulation_filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        symbol = _registered_symbol(symbol)
        timeframe = timeframe.upper()
        if timeframe not in TIMEFRAMES:
            raise RequestError(400, "Timeframe must be M1, M5, or M15.")
        from_date = _parse_date(from_value)
        to_date = _parse_date(to_value)
        if from_date > to_date:
            raise RequestError(400, "The start date must be on or before the end date.")
        active_settings = SimulationSettings.from_payload(simulation_settings)
        active_filters = SimulationFilters.from_payload(simulation_filters)
        if self.monthly_mode and symbol == "XAUUSD":
            try:
                source_data = load_monthly_timeframe(
                    self.data_dir,
                    symbol,
                    timeframe,
                    start_month=from_date.strftime("%Y-%m"),
                    end_month=to_date.strftime("%Y-%m"),
                    manifest=self.monthly_manifest,
                )
            except ValueError as exc:
                raise RequestError(400, str(exc)) from exc
        else:
            source_data = self._data_for(symbol, timeframe)
        try:
            selected = backtest.filter_candles_by_date_window(
                source_data.candles, from_date, to_date
            )
        except ValueError as exc:
            raise RequestError(400, str(exc)) from exc
        if len(selected) > MAX_LIMIT:
            raise RequestError(
                413,
                f"This window contains more than {MAX_LIMIT:,} candles. Choose a shorter date range.",
            )
        self._require_approved_market_data(symbol)
        loaded = {(symbol, timeframe): source_data}
        smoke = backtest.run_smoke_window(
            loaded=loaded,
            spec={**self.spec, "instruments": [symbol], "timeframes": [timeframe]},
            from_date=from_date,
            to_date=to_date,
        )
        candles = smoke["candles_by_pair"][(symbol, timeframe)]
        if self.monthly_mode and symbol == "XAUUSD":
            trend_states = {}
            for month in month_sequence(
                from_date.strftime("%Y-%m"), to_date.strftime("%Y-%m")
            ):
                month_data = self._load_monthly_month(symbol, timeframe, month)
                trend_states.update(
                    self._trend_states_for_month(
                        timeframe, symbol, month, month_data
                    )
                )
        else:
            trend_states = self._trend_states_for_timeframe(timeframe, symbol)
        for raw in smoke["alerts"]:
            state = trend_states[str(raw["time"])]
            raw["dc_state"] = state["dcState"]
            raw["hier_state"] = state["hierState"]
            raw["hier_promotions"] = state["hierPromotions"]
        alerts: list[dict[str, Any]] = []
        for raw in smoke["alerts"]:
            alert_candle = raw["candle"]
            previous_candle = raw["previous_candle"]
            alerts.append(
                {
                    "alertId": raw["alert_id"],
                    "time": raw["time"],
                    "symbol": raw["symbol"],
                    "timeframe": raw["tf"],
                    "direction": raw["direction"],
                    "alertType": raw["alert_type"],
                    "cct": raw["cct"],
                    "cst": raw["cst"],
                    "dcState": raw["dc_state"],
                    "hierState": raw["hier_state"],
                    "strengthLabel": raw["strength_label"],
                    "note": raw["note"] or None,
                    "candle": _serialize_candle(alert_candle),
                    "previousCandle": _serialize_candle(previous_candle),
                    "scored": False,
                }
            )
        candle_indexes = {
            str(candle["time"]): index for index, candle in enumerate(candles)
        }
        alerts_by_index: dict[int, list[dict[str, Any]]] = {}
        for raw in smoke["alerts"]:
            candle_index = candle_indexes.get(str(raw["time"]))
            if candle_index is not None:
                alerts_by_index.setdefault(candle_index, []).append(raw)
        simulation_result = simulate_batch(
            candles,
            alerts_by_index,
            settings=active_settings.to_dict(),
            filters=active_filters,
        )
        wick_pressure = StreamingWickPressure()
        wick_pressure_by_time = {
            candle.time: str(wick_pressure.update(candle))
            for candle in backtest.candle_objects(candles)
        }
        append_batch_snapshots(
            smoke["alerts"],
            path=self.output_dir / "snapshots.jsonl",
            wick_pressure_by_time=wick_pressure_by_time,
            atr14_by_alert_id=simulation_result["atr14_by_alert_id"],
            trades=simulation_result["trades"],
        )
        counts = {
            "buy": sum(alert["direction"] == "BUY" for alert in alerts),
            "sell": sum(alert["direction"] == "SELL" for alert in alerts),
            "strong": sum(alert["strengthLabel"] == "STRONG" for alert in alerts),
            "neutral": sum(alert["strengthLabel"] == "NEUTRAL" for alert in alerts),
            "weak": sum(alert["strengthLabel"] == "WEAK" for alert in alerts),
        }
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "from": from_value,
            "to": to_value,
            "candles": [
                _serialize_candle(candle, trend_states[str(candle["time"])])
                for candle in candles
            ],
            "alerts": alerts,
            "counts": counts,
            "totalCandles": len(candles),
            "truncated": False,
            "status": "SMOKE_NOT_SCORED",
            "runId": smoke["run_id"],
            "specVersion": str(self.spec["version"]),
            "source": source_data.source,
            "sourceSha256": source_data.sha256,
            "ownerAttestedSanityMetrics": dict(OWNER_ATTESTED_SANITY_METRICS),
            "simulation": simulation_result["simulation"],
            "simulatedTrades": simulation_result["trades"],
            "durationMs": int((time.perf_counter() - started) * 1000),
        }

    def _find_trade_snapshot(self, trade_id: str) -> dict[str, Any] | None:
        snapshot_path = self.output_dir / "snapshots.jsonl"
        latest: dict[str, Any] | None = None
        try:
            with snapshot_path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        candidate = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed trade snapshot row.")
                        continue
                    if (
                        isinstance(candidate, dict)
                        and candidate.get("sim_trade_id") == trade_id
                    ):
                        latest = candidate
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("Trade snapshots unavailable: %s", type(exc).__name__)
            return None
        return latest

    def trade_comment(self, trade_id: str) -> dict[str, Any]:
        if not isinstance(trade_id, str) or not trade_id.strip() or len(trade_id) > 256:
            raise RequestError(400, "Trade is missing its identifier.")
        trade_id = trade_id.strip()
        source_snapshot = self._find_trade_snapshot(trade_id)
        if not source_snapshot:
            return self._unavailable_trade_comment(
                trade_id, "No saved trade snapshot is available."
            )
        if source_snapshot.get("sim_outcome") not in {"WIN", "LOSS"}:
            return self._unavailable_trade_comment(
                trade_id, "A resolved trade snapshot is required."
            )
        snapshot = {
            key: source_snapshot[key]
            for key in TRADE_COMMENT_SNAPSHOT_FIELDS
            if key in source_snapshot
        }
        if not snapshot.get("symbol") or not snapshot.get("direction"):
            return self._unavailable_trade_comment(
                trade_id, "Trade context is incomplete."
            )

        cache_key = f"trade:{trade_id}"
        context_payload = json.dumps(
            {"promptVersion": 1, "snapshot": snapshot},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        context_hash = hashlib.sha256(context_payload.encode("utf-8")).hexdigest()
        cache_path = self.output_dir / "ai_cache.json"
        usage_path = self.output_dir / "ai_token_usage.jsonl"

        with self.cache_lock:
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                if not isinstance(cache, dict):
                    raise ValueError("AI comment cache must be a JSON object.")
            except FileNotFoundError:
                cache = {}
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                logger.warning("AI comment cache unavailable: %s", type(exc).__name__)
                return self._unavailable_trade_comment(trade_id)

            cached = cache.get(cache_key)
            if (
                isinstance(cached, dict)
                and cached.get("contextSha256") == context_hash
                and isinstance(cached.get("comment"), str)
            ):
                return {
                    "available": True,
                    "tradeId": trade_id,
                    "comment": cached["comment"],
                    "cached": True,
                    "tokenUsage": None,
                    "message": None,
                }

            try:
                response = llm_client.propose(_trade_comment_prompt(snapshot))
                comment = _normalise_trade_comment(response.rationale)
                if not comment:
                    raise ValueError("The provider returned a note outside journal rules.")
            except Exception as exc:
                logger.warning("Trade AI note unavailable: %s", type(exc).__name__)
                return self._unavailable_trade_comment(trade_id)

            cache[cache_key] = {
                "contextSha256": context_hash,
                "comment": comment,
            }
            self.output_dir.mkdir(parents=True, exist_ok=True)
            temporary_cache = cache_path.with_suffix(".json.tmp")
            try:
                temporary_cache.write_text(
                    json.dumps(cache, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                temporary_cache.replace(cache_path)
            except OSError as exc:
                logger.warning("Trade AI note cache write failed: %s", type(exc).__name__)

            provider_usage = response.usage
            token_usage = None
            if all(
                isinstance(provider_usage.get(key), int)
                and not isinstance(provider_usage.get(key), bool)
                for key in ("input", "output", "total")
            ):
                token_usage = {
                    "promptTokens": provider_usage["input"],
                    "responseTokens": provider_usage["output"],
                    "totalTokens": provider_usage["total"],
                }
            usage_record = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "tradeId": trade_id,
                "provider": response.provider,
                "promptTokens": token_usage["promptTokens"] if token_usage else None,
                "responseTokens": token_usage["responseTokens"] if token_usage else None,
                "totalTokens": token_usage["totalTokens"] if token_usage else None,
            }
            try:
                with usage_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(usage_record, sort_keys=True) + "\n")
            except OSError as exc:
                logger.warning("Trade AI usage log unavailable: %s", type(exc).__name__)
            return {
                "available": True,
                "tradeId": trade_id,
                "comment": comment,
                "cached": False,
                "tokenUsage": token_usage,
                "message": None,
            }

    @staticmethod
    def _unavailable_trade_comment(
        trade_id: str, message: str = "AI assist unavailable"
    ) -> dict[str, Any]:
        return {
            "available": False,
            "tradeId": trade_id,
            "comment": None,
            "cached": False,
            "tokenUsage": None,
            "message": message,
        }

    def alert_comment(self, alert: dict[str, Any]) -> dict[str, Any]:
        alert_id = alert.get("alertId")
        if not isinstance(alert_id, str) or not alert_id:
            raise RequestError(400, "Alert is missing its identifier.")
        _registered_symbol(alert.get("symbol"))
        if alert.get("timeframe") not in TIMEFRAMES:
            raise RequestError(400, "Alert timeframe must be M1, M5, or M15.")
        if alert.get("direction") not in {"BUY", "SELL"}:
            raise RequestError(400, "Alert direction must be BUY or SELL.")
        if alert.get("strengthLabel") not in {"STRONG", "NEUTRAL", "WEAK"}:
            raise RequestError(400, "Alert strength label is invalid.")
        if not isinstance(alert.get("candle"), dict) or not isinstance(
            alert.get("previousCandle"), dict
        ):
            raise RequestError(400, "Alert OHLC context is incomplete.")
        context_payload = json.dumps(
            alert, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        context_hash = hashlib.sha256(context_payload.encode("utf-8")).hexdigest()
        cache_path = self.output_dir / "ai_cache.json"
        usage_path = self.output_dir / "ai_token_usage.jsonl"

        with self.cache_lock:
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                if not isinstance(cache, dict):
                    raise ValueError("AI comment cache must be a JSON object.")
            except FileNotFoundError:
                cache = {}
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                logger.warning("AI comment cache unavailable: %s", type(exc).__name__)
                return self._unavailable_comment(alert_id)

            cached = cache.get(alert_id)
            if (
                isinstance(cached, dict)
                and cached.get("contextSha256") == context_hash
                and isinstance(cached.get("comment"), str)
            ):
                return {
                    "available": True,
                    "alertId": alert_id,
                    "comment": cached["comment"],
                    "cached": True,
                    "tokenUsage": None,
                    "message": None,
                }

            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                return self._unavailable_comment(alert_id)
            try:
                comment, usage = self.ai_generator(alert, api_key)
                comment = _normalise_comment(comment)
                if not comment:
                    raise RuntimeError("Empty AI response")
            except Exception as exc:
                logger.warning("AI assist unavailable: %s", type(exc).__name__)
                return self._unavailable_comment(alert_id)

            cache[alert_id] = {
                "contextSha256": context_hash,
                "comment": comment,
            }
            self.output_dir.mkdir(parents=True, exist_ok=True)
            temporary_cache = cache_path.with_suffix(".json.tmp")
            temporary_cache.write_text(
                json.dumps(cache, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary_cache.replace(cache_path)
            usage_record = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "alertId": alert_id,
                "model": GEMINI_MODEL,
                **usage,
            }
            with usage_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(usage_record, sort_keys=True) + "\n")
            logger.info(
                "Gemini token usage alert=%s prompt=%d response=%d total=%d",
                alert_id,
                usage["promptTokens"],
                usage["responseTokens"],
                usage["totalTokens"],
            )
            return {
                "available": True,
                "alertId": alert_id,
                "comment": comment,
                "cached": False,
                "tokenUsage": usage,
                "message": None,
            }

    @staticmethod
    def _unavailable_comment(alert_id: str) -> dict[str, Any]:
        return {
            "available": False,
            "alertId": alert_id,
            "comment": None,
            "cached": False,
            "tokenUsage": None,
            "message": "AI assist unavailable",
        }


ControlRoomService = LabService


def make_handler(service: LabService) -> type[BaseHTTPRequestHandler]:
    class LabRequestHandler(BaseHTTPRequestHandler):
        server_version = "ForexStrategyLab/1.0"

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode(
                "utf-8"
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, message: str) -> None:
            self._send_json(status, {"error": message})

        def _read_json(self) -> dict[str, Any]:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise RequestError(400, "Invalid Content-Length.") from exc
            if content_length < 1 or content_length > MAX_REQUEST_BYTES:
                raise RequestError(400, "Request body is empty or too large.")
            if self.headers.get_content_type() != "application/json":
                raise RequestError(400, "Content-Type must be application/json.")
            try:
                value = json.loads(self.rfile.read(content_length))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RequestError(400, "Request body must contain valid JSON.") from exc
            if not isinstance(value, dict):
                raise RequestError(400, "Request body must be a JSON object.")
            return value

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            try:
                if parsed.path == "/api/healthz":
                    self._send_json(200, {"status": "ok"})
                    return
                if parsed.path == "/api/evolve/policy":
                    self._send_json(200, service.current_policy())
                    return
                if parsed.path == "/api/evolve/history":
                    proposals_dir = getattr(
                        service, "evolve_proposals_dir", EVOLVE_PROPOSALS_DIR
                    )
                    self._send_json(
                        200, {"rounds": _evolve_round_history(Path(proposals_dir))}
                    )
                    return
                path_parts = parsed.path.strip("/").split("/")
                if (
                    len(path_parts) == 4
                    and path_parts[:3] == ["api", "evolve", "status"]
                ):
                    self._send_json(
                        200, service.evolve_jobs.get_status(path_parts[3])
                    )
                    return
                if parsed.path == "/api/lab/dataset":
                    params = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
                    symbol = _registered_symbol(
                        _optional_query(params, "symbol", "XAUUSD")
                    )
                    self._send_json(200, service.dataset_info(symbol))
                    return
                if parsed.path == "/api/lab/candles":
                    params = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
                    symbol = _registered_symbol(
                        _optional_query(params, "symbol", "XAUUSD")
                    )
                    timeframe = _single_query(params, "timeframe")
                    from_value = _single_query(params, "from")
                    to_value = _single_query(params, "to")
                    limit_values = params.get("limit", [str(DEFAULT_LIMIT)])
                    if len(limit_values) != 1:
                        raise RequestError(400, "Limit must be specified once.")
                    try:
                        limit = int(limit_values[0])
                    except ValueError as exc:
                        raise RequestError(400, "Limit must be a whole number.") from exc
                    chunk_month_values = params.get("chunkMonth", [])
                    if len(chunk_month_values) > 1:
                        raise RequestError(400, "Chunk month must be specified once.")
                    chunk_month = (
                        chunk_month_values[0] if chunk_month_values else None
                    )
                    self._send_json(
                        200,
                        service.candle_window(
                            timeframe,
                            from_value,
                            to_value,
                            limit=limit,
                            chunk_month=chunk_month,
                            symbol=symbol,
                        ),
                    )
                    return
                self._error(404, "Route not found.")
            except RequestError as exc:
                self._error(exc.status, str(exc))
            except EvolveJobError as exc:
                self._error(exc.status, str(exc))
            except ValueError as exc:
                self._error(400, str(exc))
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                logger.exception("Unhandled GET request failure path=%s", parsed.path)
                self._error(500, "The lab request could not be completed.")

        def do_POST(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            try:
                payload = self._read_json()
                if parsed.path == "/api/evolve/run":
                    if set(payload) - {"symbol"}:
                        raise RequestError(
                            400, "Only the selected symbol is accepted."
                        )
                    symbol = _registered_symbol(payload.get("symbol", "XAUUSD"))
                    data_dir = getattr(service, "data_dir", DATA_ROOT)
                    self._send_json(
                        202,
                        service.evolve_jobs.start(
                            symbol, data_root=data_dir
                        ),
                    )
                    return
                if parsed.path == "/api/evolve/decide":
                    job_id = payload.get("job_id")
                    promote = payload.get("promote")
                    if not isinstance(job_id, str) or not job_id:
                        raise RequestError(400, "job_id is required.")
                    if not isinstance(promote, bool):
                        raise RequestError(400, "promote must be a boolean.")
                    self._send_json(
                        202, service.evolve_jobs.decide(job_id, promote)
                    )
                    return
                if parsed.path == "/api/lab/replay/sessions":
                    symbol = _registered_symbol(payload.get("symbol", "XAUUSD"))
                    timeframe = payload.get("timeframe")
                    from_value = payload.get("from")
                    to_value = payload.get("to")
                    if not all(
                        isinstance(value, str)
                        for value in (timeframe, from_value, to_value)
                    ):
                        raise RequestError(
                            400, "Timeframe, from, and to are required strings."
                        )
                    variant_b = payload.get("variant_b", False)
                    if not isinstance(variant_b, bool):
                        raise RequestError(400, "variant_b must be a boolean.")
                    simulation_settings = payload.get("simulationSettings")
                    if simulation_settings is not None and not isinstance(
                        simulation_settings, dict
                    ):
                        raise RequestError(
                            400, "simulationSettings must be an object."
                        )
                    self._send_json(
                        201,
                        service.create_replay_session(
                            timeframe,
                            from_value,
                            to_value,
                            symbol=symbol,
                            variant_b=variant_b,
                            simulation_settings=simulation_settings,
                        ),
                    )
                    return
                path_parts = parsed.path.strip("/").split("/")
                if (
                    len(path_parts) == 6
                    and path_parts[:4] == ["api", "lab", "replay", "sessions"]
                    and path_parts[5] == "advance"
                ):
                    to_index = payload.get("toIndex")
                    view_start = payload.get("viewStart")
                    view_end = payload.get("viewEnd")
                    indexes = (to_index, view_start, view_end)
                    if any(
                        not isinstance(index, int) or isinstance(index, bool)
                        for index in indexes
                    ):
                        raise RequestError(
                            400, "toIndex, viewStart, and viewEnd must be integers."
                        )
                    simulation_filters = payload.get("simulationFilters")
                    if simulation_filters is not None and not isinstance(
                        simulation_filters, dict
                    ):
                        raise RequestError(
                            400, "simulationFilters must be an object."
                        )
                    self._send_json(
                        200,
                        service.advance_replay_session(
                            path_parts[4],
                            to_index=to_index,
                            view_start=view_start,
                            view_end=view_end,
                            simulation_filters=simulation_filters,
                        ),
                    )
                    return
                if parsed.path == "/api/lab/backtest":
                    symbol = _registered_symbol(payload.get("symbol", "XAUUSD"))
                    timeframe = payload.get("timeframe")
                    from_value = payload.get("from")
                    to_value = payload.get("to")
                    if not all(isinstance(value, str) for value in (
                        timeframe,
                        from_value,
                        to_value,
                    )):
                        raise RequestError(
                            400, "Timeframe, from, and to are required strings."
                        )
                    simulation_settings = payload.get("simulationSettings")
                    if simulation_settings is not None and not isinstance(
                        simulation_settings, dict
                    ):
                        raise RequestError(
                            400, "simulationSettings must be an object."
                        )
                    simulation_filters = payload.get("simulationFilters")
                    if simulation_filters is not None and not isinstance(
                        simulation_filters, dict
                    ):
                        raise RequestError(
                            400, "simulationFilters must be an object."
                        )
                    self._send_json(
                        200,
                        service.backtest_window(
                            timeframe,
                            from_value,
                            to_value,
                            symbol=symbol,
                            simulation_settings=simulation_settings,
                            simulation_filters=simulation_filters,
                        ),
                    )
                    return
                if parsed.path == "/api/lab/alerts/comment":
                    alert = payload.get("alert")
                    if not isinstance(alert, dict):
                        raise RequestError(400, "Alert context is required.")
                    self._send_json(200, service.alert_comment(alert))
                    return
                if parsed.path == "/api/lab/trades/comment":
                    trade_id = payload.get("tradeId")
                    if not isinstance(trade_id, str):
                        raise RequestError(400, "tradeId is required.")
                    self._send_json(200, service.trade_comment(trade_id))
                    return
                self._error(404, "Route not found.")
            except RequestError as exc:
                self._error(exc.status, str(exc))
            except EvolveJobError as exc:
                self._error(exc.status, str(exc))
            except ValueError as exc:
                self._error(400, str(exc))
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                logger.exception("Unhandled POST request failure path=%s", parsed.path)
                self._error(500, "The lab request could not be completed.")

        def log_message(self, _format: str, *args: Any) -> None:
            logger.info(
                "http method=%s path=%s",
                self.command,
                urllib.parse.urlsplit(self.path).path,
            )

    return LabRequestHandler


def _single_query(params: dict[str, list[str]], name: str) -> str:
    values = params.get(name)
    if not values or len(values) != 1 or not values[0]:
        raise RequestError(400, f"Query parameter '{name}' is required once.")
    return values[0]


def serve(
    service: LabService | None = None,
    *,
    host: str = "0.0.0.0",
    port: int | None = None,
) -> None:
    resolved_port = port if port is not None else int(os.environ.get("PORT", "8080"))
    lab_service = service or LabService()
    server = ThreadingHTTPServer((host, resolved_port), make_handler(lab_service))
    server.daemon_threads = True
    logger.info("Forex Strategy Lab API listening on port %d", resolved_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Forex Strategy Lab API shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    serve()
