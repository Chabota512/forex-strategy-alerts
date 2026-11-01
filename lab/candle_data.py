"""Provider-neutral candle loading from local, normalized OHLC CSV files."""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

if __package__:
    from .engine import Candle, CandleDataError
else:
    from engine import Candle, CandleDataError

LAB_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = LAB_DIR / "data"
REQUIRED_PRICE_COLUMNS = {"open", "high", "low", "close"}
TIME_COLUMN_ALIASES = ("time", "timestamp", "timestamp_utc")
SAFE_NAME = re.compile(r"^[A-Za-z0-9]+$")


class CandleRecord(TypedDict):
    time: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class ResampleCandleRecord(CandleRecord):
    timestamp: datetime
    source: str


@dataclass(frozen=True)
class CandleFileData:
    candles: list[ResampleCandleRecord]
    path: Path
    source: str
    sha256: str


def parse_timestamp(value: str, path: Path, line_number: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{path}:{line_number}: invalid ISO timestamp {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_data_file(data_dir: Path, symbol: str, timeframe: str) -> Path:
    symbol = symbol.upper()
    timeframe = timeframe.upper()
    if not SAFE_NAME.fullmatch(symbol) or not SAFE_NAME.fullmatch(timeframe):
        raise ValueError("symbol and timeframe must contain only letters and numbers")

    canonical_path = data_dir / f"{symbol}_{timeframe}.csv"
    if canonical_path.is_file():
        return canonical_path
    raise FileNotFoundError(
        f"No local candles for {symbol} {timeframe}. Expected "
        f"{canonical_path.name} in {data_dir}."
    )


def load_candle_file(path: Path) -> CandleFileData:
    """Read OHLC CSV aliases into one resampler-ready candle representation."""
    candles: list[ResampleCandleRecord] = []
    source_values: set[str] = set()
    has_blank_source = False
    previous_timestamp: datetime | None = None

    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        headers = {
            (name or "").strip().lower(): name
            for name in (reader.fieldnames or [])
            if name is not None
        }
        time_columns = [
            headers[alias] for alias in TIME_COLUMN_ALIASES if alias in headers
        ]
        if len(time_columns) > 1:
            raise ValueError(
                f"{path}: use only one timestamp column "
                f"({', '.join(TIME_COLUMN_ALIASES)})"
            )
        time_column = time_columns[0] if time_columns else None
        missing = REQUIRED_PRICE_COLUMNS - headers.keys()
        if time_column is None:
            missing.add("one of time, timestamp, timestamp_utc")
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")
        source_column = headers.get("source")
        has_source_column = source_column is not None

        for line_number, row in enumerate(reader, start=2):
            raw_time = row.get(time_column) if time_column else None
            if not raw_time:
                raise ValueError(f"{path}:{line_number}: timestamp is empty")
            timestamp = parse_timestamp(raw_time, path, line_number)
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError(
                    f"{path}:{line_number}: timestamps must be strictly increasing"
                )
            previous_timestamp = timestamp

            try:
                candle = Candle.from_values(
                    format_utc_timestamp(timestamp),
                    row.get(headers["open"]) or "",
                    row.get(headers["high"]) or "",
                    row.get(headers["low"]) or "",
                    row.get(headers["close"]) or "",
                )
            except CandleDataError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc

            label = (
                (row.get(source_column) or "").strip().lower()
                if source_column
                else ""
            )
            candles.append(
                {
                    "timestamp": timestamp,
                    "time": candle.time,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "source": label,
                }
            )

            if has_source_column:
                if label:
                    source_values.add(label)
                else:
                    has_blank_source = True

    if not candles:
        raise ValueError(f"{path}: no candles found")
    if len(source_values) > 1 or (source_values and has_blank_source):
        raise ValueError(f"{path}: source labels must be present and consistent")

    if source_values:
        source = next(iter(source_values))
    elif path.name.startswith("synthetic_"):
        source = "synthetic"
    else:
        source = "unverified_real"

    for candle in candles:
        candle["source"] = source

    return CandleFileData(
        candles=candles,
        path=path,
        source=source,
        sha256=sha256_file(path),
    )


def load_candles_with_metadata(
    symbol: str,
    timeframe: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> CandleFileData:
    symbol = symbol.upper()
    timeframe = timeframe.upper()
    if not SAFE_NAME.fullmatch(symbol) or not SAFE_NAME.fullmatch(timeframe):
        raise ValueError("symbol and timeframe must contain only letters and numbers")

    monthly_dir = data_dir / symbol / timeframe
    if monthly_dir.is_dir():
        if __package__:
            from .monthly_data import load_monthly_timeframe
        else:
            from monthly_data import load_monthly_timeframe
        return load_monthly_timeframe(data_dir, symbol, timeframe)

    path = resolve_data_file(data_dir, symbol, timeframe)
    return load_candle_file(path)


def load_candles(symbol: str, timeframe: str) -> list[CandleRecord]:
    """Load local candles as {time, open, high, low, close}; never accesses an API."""
    candles = load_candles_with_metadata(
        symbol,
        timeframe,
        data_dir=DEFAULT_DATA_DIR,
    ).candles
    return [
        {
            "time": candle["time"],
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
        }
        for candle in candles
    ]
