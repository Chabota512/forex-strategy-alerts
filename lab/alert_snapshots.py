"""Durable feature snapshots for alerts produced by batch and replay runs."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from .engine import Candle, body_engulfs_previous

DEFAULT_SNAPSHOT_PATH = Path(__file__).resolve().parent / "output" / "snapshots.jsonl"

_SNAPSHOT_LOCK = threading.RLock()
_ROW_COUNTS: dict[Path, tuple[int, int, int, int]] = {}


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"Snapshot value is not numeric: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"Snapshot value must be finite: {value!r}")
    return parsed


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _required_decimal(record: Mapping[str, Any], key: str) -> Decimal:
    value = _decimal(record.get(key))
    if value is None:
        raise ValueError(f"Snapshot candle is missing {key}.")
    return value


def _get(alert: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in alert:
            return alert[key]
    return default


def _ohlc(record: Mapping[str, Any]) -> dict[str, str]:
    return {
        key: format(_required_decimal(record, key), "f")
        for key in ("open", "high", "low", "close")
    }


def _trade_snapshot_fields(trade: Mapping[str, Any] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "sim_trade_id": None,
        "sim_outcome": None,
        "r_multiple_achieved": None,
        "sim_r_after_costs": None,
        "sim_net_pnl": None,
    }
    if trade is None:
        return fields

    trade_id = _get(trade, "tradeId", "trade_id")
    fields["sim_trade_id"] = str(trade_id) if trade_id else None
    outcome = str(trade.get("outcome", "")).upper()
    if outcome and outcome != "OPEN":
        fields["sim_outcome"] = outcome
        fields["r_multiple_achieved"] = _decimal_text(
            _decimal(_get(trade, "realizedR", "realized_r"))
        )
        fields["sim_r_after_costs"] = _decimal_text(
            _decimal(_get(trade, "netR", "net_r"))
        )
        fields["sim_net_pnl"] = _decimal_text(
            _decimal(_get(trade, "netPnl", "net_pnl"))
        )
    return fields


def trade_snapshot_updates(trade: Mapping[str, Any]) -> dict[str, Any]:
    """Return the simulation fields to merge into an in-memory alert snapshot."""
    return _trade_snapshot_fields(trade)


def build_alert_snapshot(
    alert: Mapping[str, Any],
    *,
    atr14: Any,
    wp_state: str | None,
    trade: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candle = alert.get("candle")
    previous = _get(alert, "previous_candle", "previousCandle")
    if not isinstance(candle, Mapping) or not isinstance(previous, Mapping):
        raise ValueError("Alert snapshot requires current and previous candle OHLC.")

    current_candle = Candle.from_values(
        str(alert.get("time", candle.get("time", ""))),
        _required_decimal(candle, "open"),
        _required_decimal(candle, "high"),
        _required_decimal(candle, "low"),
        _required_decimal(candle, "close"),
    )
    previous_candle = Candle.from_values(
        str(previous.get("time", "")),
        _required_decimal(previous, "open"),
        _required_decimal(previous, "high"),
        _required_decimal(previous, "low"),
        _required_decimal(previous, "close"),
    )
    candle_range = current_candle.high - current_candle.low
    body_size = abs(current_candle.close - current_candle.open)
    lower_wick = min(current_candle.open, current_candle.close) - current_candle.low
    upper_wick = current_candle.high - max(current_candle.open, current_candle.close)
    if candle_range > 0:
        body_ratio = body_size / candle_range
        lower_wick_frac = lower_wick / candle_range
        upper_wick_frac = upper_wick / candle_range
    else:
        body_ratio = lower_wick_frac = upper_wick_frac = None

    atr = _decimal(atr14)
    direction = str(alert.get("direction", ""))
    swept_distance = (
        previous_candle.low - current_candle.low
        if direction == "BUY"
        else current_candle.high - previous_candle.high
        if direction == "SELL"
        else Decimal("0")
    )
    penetration_atr = (
        max(swept_distance, Decimal("0")) / atr
        if atr is not None and atr > 0
        else None
    )

    timestamp = str(alert.get("time", current_candle.time))
    parsed_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed_time.tzinfo is None:
        parsed_time = parsed_time.replace(tzinfo=timezone.utc)
    parsed_time = parsed_time.astimezone(timezone.utc)

    alert_type = str(_get(alert, "alert_type", "alertType", default=""))
    period = str(alert.get("period", "")).strip()
    if not period or period == "SMOKE":
        period = timestamp[:7]
    engulf = alert.get("engulfing")
    if not isinstance(engulf, bool):
        engulf = body_engulfs_previous(previous_candle, current_candle, direction)

    snapshot = {
        "alert_id": str(_get(alert, "alert_id", "alertId", default="")),
        "symbol": str(alert.get("symbol", "")),
        "tf": str(_get(alert, "tf", "timeframe", default="")),
        "time": timestamp,
        "direction": direction,
        "variant": "B" if alert_type.endswith("-B") else "A",
        "strength": str(_get(alert, "strength_label", "strengthLabel", "strength", default="")),
        "engulf": bool(engulf),
        "period": period,
        "input_source": str(_get(alert, "input_source", "source", default="")),
        "input_sha256": str(_get(alert, "input_sha256", "source_sha256", default="")),
        "run_id": str(alert.get("run_id", "")),
        "alert_candle_ohlc": _ohlc(candle),
        "prev_candle_ohlc": _ohlc(previous),
        "cct": str(alert.get("cct", "")),
        "cst": str(alert.get("cst", "")),
        "mtf_cst": _get(alert, "mtf_cst", "mtfCst"),
        "mtf_cct": _get(alert, "mtf_cct", "mtfCct"),
        "mtf_wick_pressure": _get(
            alert, "mtf_wick_pressure", "mtfWickPressure"
        ),
        "mtf_closed_at": _get(alert, "mtf_closed_at", "mtfClosedAt"),
        "mtf_trend_agree": bool(
            _get(alert, "mtf_trend_agree", "mtfTrendAgree", default=False)
        ),
        "mtf_cct_agree": bool(
            _get(alert, "mtf_cct_agree", "mtfCctAgree", default=False)
        ),
        "mtf_wp_agree": bool(
            _get(alert, "mtf_wp_agree", "mtfWpAgree", default=False)
        ),
        "dual_tf_alert": bool(
            _get(alert, "dual_tf_alert", "dualTfAlert", default=False)
        ),
        "dc_state": str(alert.get("dc_state", "undefined")),
        "hier_state": str(alert.get("hier_state", "undefined")),
        "hier_promotions": list(alert.get("hier_promotions", [])),
        "wp_state": str(wp_state) if wp_state is not None else None,
        "atr14_at_alert": _decimal_text(atr),
        "body_ratio": _decimal_text(body_ratio),
        "lower_wick_frac": _decimal_text(lower_wick_frac),
        "upper_wick_frac": _decimal_text(upper_wick_frac),
        "penetration_atr": _decimal_text(penetration_atr),
        "hour_utc": parsed_time.hour,
        "day_of_week": parsed_time.weekday(),
    }
    snapshot.update(_trade_snapshot_fields(trade))
    return snapshot


def _file_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size


def _count_existing_rows(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.endswith(b"\n"):
                raise ValueError(
                    f"Existing snapshot log has an incomplete line {line_number}: {path}"
                )
            try:
                json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Existing snapshot log has invalid JSON on line {line_number}: {path}"
                ) from exc
            count += 1
    return count


def append_snapshot(path: Path, snapshot: Mapping[str, Any]) -> int:
    """Append one complete JSONL record and return its zero-based row index."""
    path = Path(path)
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    with _SNAPSHOT_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        identity = _file_identity(path)
        cached = _ROW_COUNTS.get(path)
        if cached is None or cached[:3] != (identity or (0, 0, 0)):
            row_count = _count_existing_rows(path)
        else:
            row_count = cached[3]
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")
            stream.flush()
        new_identity = _file_identity(path) or (0, 0, 0)
        _ROW_COUNTS[path] = (*new_identity, row_count + 1)
        return row_count


def read_snapshots(path: Path) -> list[dict[str, Any]]:
    """Read and validate a JSONL snapshot log."""
    path = Path(path)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with _SNAPSHOT_LOCK, path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.endswith("\n"):
                raise ValueError(
                    f"Incomplete JSONL record on line {line_number}: {path}"
                )
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in snapshot log line {line_number}: {path}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"Snapshot log line {line_number} is not an object: {path}"
                )
            rows.append(row)
    return rows


def append_batch_snapshots(
    alerts: Sequence[Mapping[str, Any]],
    *,
    path: Path,
    wick_pressure_by_time: Mapping[str, str],
    atr14_by_alert_id: Mapping[str, Any],
    trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Persist final batch snapshots using the same builder as replay sessions."""
    trades_by_alert_id = {
        str(_get(trade, "alertId", "alert_id", default="")): trade
        for trade in trades
        if _get(trade, "alertId", "alert_id")
    }
    snapshots: list[dict[str, Any]] = []
    for alert in alerts:
        alert_id = str(_get(alert, "alert_id", "alertId", default=""))
        timestamp = str(alert.get("time", ""))
        snapshot = build_alert_snapshot(
            alert,
            atr14=atr14_by_alert_id.get(alert_id),
            wp_state=wick_pressure_by_time.get(timestamp),
            trade=trades_by_alert_id.get(alert_id),
        )
        append_snapshot(path, snapshot)
        snapshots.append(snapshot)
    return snapshots

