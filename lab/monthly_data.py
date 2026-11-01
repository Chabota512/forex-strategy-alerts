"""Build and load XAUUSD data in verified calendar-month files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from .candle_data import (
    CandleFileData,
    DEFAULT_DATA_DIR,
    format_utc_timestamp,
    load_candle_file,
    parse_timestamp,
    sha256_file,
)
from .resample import INTERVALS, _aggregate, _utc_bucket_start, _write_candles

SYMBOL = "XAUUSD"
SOURCE = "dukascopy"
EXPECTED_MERGED_ROWS = 1_127_520
EXPECTED_START_DATE = "2023-01-02"
EXPECTED_END_DATE = "2025-12-31"
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TIMEFRAMES = ("M1", *INTERVALS.keys())
MONTHS = tuple(
    f"{year:04d}-{month:02d}"
    for year in range(2023, 2026)
    for month in range(1, 13)
)
MANIFEST_FILENAME = "monthly-manifest.json"
RAW_ARCHIVES = tuple(
    Path("historicalMarketData")
    / SYMBOL
    / f"{SYMBOL}_M1_BID_{year}_UTC.csv"
    for year in range(2023, 2026)
)


class MonthlyDataError(ValueError):
    """Raised when monthly data or its integrity manifest is invalid."""


@dataclass(frozen=True)
class MonthlyBuildResult:
    merged_sha256: str
    merged_rows: int
    month_counts: dict[str, dict[str, int]]
    month_hashes: dict[str, dict[str, str]]
    raw_archive_hashes: dict[str, str]


def month_sequence(start_month: str, end_month: str) -> tuple[str, ...]:
    """Return an inclusive, validated YYYY-MM sequence."""
    if not MONTH_PATTERN.fullmatch(start_month) or not MONTH_PATTERN.fullmatch(
        end_month
    ):
        raise MonthlyDataError("Months must use YYYY-MM format.")
    start_year, start_number = (int(part) for part in start_month.split("-"))
    end_year, end_number = (int(part) for part in end_month.split("-"))
    start_ordinal = start_year * 12 + start_number - 1
    end_ordinal = end_year * 12 + end_number - 1
    if start_ordinal > end_ordinal:
        raise MonthlyDataError("The first month must be on or before the last month.")
    return tuple(
        f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"
        for ordinal in range(start_ordinal, end_ordinal + 1)
    )


def monthly_manifest_path(data_root: Path, symbol: str = SYMBOL) -> Path:
    return data_root / symbol.upper() / MANIFEST_FILENAME


def monthly_file_path(
    data_root: Path, symbol: str, timeframe: str, month: str
) -> Path:
    normalized_symbol = symbol.strip().upper()
    normalized_timeframe = timeframe.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]+", normalized_symbol):
        raise MonthlyDataError("Symbol must contain only letters and digits.")
    if normalized_timeframe not in TIMEFRAMES:
        raise MonthlyDataError(f"Unsupported monthly timeframe {timeframe!r}.")
    if not MONTH_PATTERN.fullmatch(month):
        raise MonthlyDataError("Month must use YYYY-MM format.")
    return data_root / normalized_symbol / normalized_timeframe / f"{month}.csv"


def load_monthly_manifest(
    data_root: Path = DEFAULT_DATA_DIR, *, symbol: str = SYMBOL
) -> dict[str, Any]:
    """Load and validate the generated monthly manifest without reading all data."""
    path = monthly_manifest_path(data_root, symbol)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonthlyDataError(f"Could not read monthly manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MonthlyDataError(f"{path}: monthly manifest must be an object.")
    if (
        payload.get("status") != "OWNER_APPROVED"
        or payload.get("owner_approved") is not True
    ):
        raise MonthlyDataError(f"{path}: monthly dataset is not OWNER_APPROVED.")
    if (
        payload.get("schema_version") != 1
        or payload.get("symbol") != symbol.upper()
        or payload.get("source") != SOURCE
        or not isinstance(payload.get("source_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", payload["source_sha256"])
        or isinstance(payload.get("source_rows"), bool)
        or payload.get("source_rows") != EXPECTED_MERGED_ROWS
        or payload.get("months") != list(MONTHS)
    ):
        raise MonthlyDataError(
            f"{path}: manifest does not match the verified XAUUSD monthly source."
        )
    raw_archives = payload.get("source_raw_archives")
    expected_raw_paths = {relative.as_posix() for relative in RAW_ARCHIVES}
    if (
        not isinstance(raw_archives, dict)
        or set(raw_archives) != expected_raw_paths
        or any(
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in raw_archives.values()
        )
    ):
        raise MonthlyDataError(f"{path}: raw archive fingerprints are missing or invalid.")
    records = payload.get("timeframes")
    if not isinstance(records, dict):
        raise MonthlyDataError(f"{path}: timeframe records are missing.")
    for timeframe in TIMEFRAMES:
        month_records = records.get(timeframe)
        if not isinstance(month_records, dict) or set(month_records) != set(MONTHS):
            raise MonthlyDataError(
                f"{path}: {timeframe} must contain all 36 calendar months."
            )
        for month in MONTHS:
            record = month_records.get(month)
            if (
                not isinstance(record, dict)
                or isinstance(record.get("row_count"), bool)
                or not isinstance(record.get("row_count"), int)
                or record["row_count"] < 1
                or not isinstance(record.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
            ):
                raise MonthlyDataError(
                    f"{path}: invalid {timeframe} record for {month}."
                )
            m1_reference = record.get("m1_sha256")
            if (
                timeframe != "M1"
                and m1_reference is not None
                and m1_reference != records["M1"][month].get("sha256")
            ):
                raise MonthlyDataError(
                    f"{path}: {timeframe} source hash does not match M1 for {month}."
                )
    return payload


def load_month(
    data_root: Path,
    symbol: str,
    timeframe: str,
    month: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> CandleFileData:
    """Load one month and verify its hash, row count, and timestamp range."""
    normalized_symbol = symbol.strip().upper()
    normalized_timeframe = timeframe.strip().upper()
    if manifest is None:
        manifest = load_monthly_manifest(data_root, symbol=normalized_symbol)
    path = monthly_file_path(data_root, normalized_symbol, normalized_timeframe, month)
    record = manifest["timeframes"][normalized_timeframe][month]
    try:
        actual_hash = sha256_file(path)
        if actual_hash != record["sha256"]:
            raise MonthlyDataError(
                f"{path}: file hash differs from the monthly manifest."
            )
        data = load_candle_file(path)
    except (OSError, ValueError) as exc:
        if isinstance(exc, MonthlyDataError):
            raise
        raise MonthlyDataError(f"Could not load {normalized_symbol} {timeframe} {month}: {exc}") from exc
    if len(data.candles) != record["row_count"]:
        raise MonthlyDataError(f"{path}: row count differs from the monthly manifest.")
    if any(str(candle["time"])[:7] != month for candle in data.candles):
        raise MonthlyDataError(f"{path}: contains candles outside {month}.")
    if normalized_timeframe != "M1":
        expected_m1_hash = manifest["timeframes"]["M1"][month]["sha256"]
        if record.get("m1_sha256") is not None and record["m1_sha256"] != expected_m1_hash:
            raise MonthlyDataError(f"{path}: provenance does not match its M1 month.")
        m1_path = monthly_file_path(data_root, normalized_symbol, "M1", month)
        if sha256_file(m1_path) != expected_m1_hash:
            raise MonthlyDataError(f"{m1_path}: M1 file hash differs from the manifest.")
    if normalized_symbol == SYMBOL and data.source.lower() != SOURCE:
        raise MonthlyDataError(f"{path}: expected the {SOURCE} source label.")
    return data


def _canonical_hash(candles: Iterable[dict[str, Any]]) -> str:
    """Hash a single canonical, merged CSV stream without retaining a second copy."""
    digest = hashlib.sha256()
    digest.update(b"time,open,high,low,close,source\r\n")
    for candle in candles:
        values = (
            str(candle["time"]),
            format(candle["open"], "f"),
            format(candle["high"], "f"),
            format(candle["low"], "f"),
            format(candle["close"], "f"),
            SOURCE,
        )
        digest.update((",".join(values) + "\r\n").encode("utf-8"))
    return digest.hexdigest()


def load_monthly_timeframe(
    data_root: Path,
    symbol: str,
    timeframe: str,
    *,
    start_month: str | None = None,
    end_month: str | None = None,
    manifest: dict[str, Any] | None = None,
) -> CandleFileData:
    """Stitch verified month files in memory for backtests and evolution."""
    if manifest is None:
        manifest = load_monthly_manifest(data_root, symbol=symbol)
    available = tuple(manifest["months"])
    first_month = start_month or available[0]
    last_month = end_month or available[-1]
    months = month_sequence(first_month, last_month)
    missing = [month for month in months if month not in available]
    if missing:
        raise MonthlyDataError(
            f"{symbol} {timeframe} is missing calendar months: {', '.join(missing)}."
        )
    files = [
        load_month(
            data_root,
            symbol,
            timeframe,
            month,
            manifest=manifest,
        )
        for month in months
    ]
    candles = [candle for file_data in files for candle in file_data.candles]
    if not candles:
        raise MonthlyDataError(f"No candles found for {symbol} {timeframe}.")
    for earlier, later in zip(candles, candles[1:]):
        if str(earlier["time"]) >= str(later["time"]):
            raise MonthlyDataError(
                f"{symbol} {timeframe} monthly files overlap or are out of order."
            )
    digest = _canonical_hash(candles)
    return CandleFileData(
        candles=candles,
        path=files[0].path,
        source=files[0].source,
        sha256=digest,
    )


def verify_monthly_gate(
    data_root: Path = DEFAULT_DATA_DIR,
    *,
    manifest: dict[str, Any] | None = None,
) -> str:
    """Verify monthly M1 rows against the frozen raw archives as data."""
    manifest = manifest or load_monthly_manifest(data_root)
    if (
        manifest.get("status") != "OWNER_APPROVED"
        or manifest.get("owner_approved") is not True
    ):
        raise MonthlyDataError("XAUUSD monthly dataset is not OWNER_APPROVED.")
    verify_monthly_file_hashes(data_root, manifest=manifest)
    actual_archive_hashes = _raw_archive_hashes(data_root)
    expected_archive_hashes = manifest["source_raw_archives"]
    for relative_path, actual_hash in actual_archive_hashes.items():
        if expected_archive_hashes.get(relative_path) != actual_hash:
            raise MonthlyDataError(
                f"{relative_path}: raw archive hash differs from the manifest."
            )

    raw_rows = iter(_iter_raw_archives(data_root))
    digest = hashlib.sha256()
    row_count = 0
    first_time: str | None = None
    last_time: str | None = None
    previous_monthly_time = ""
    for month in MONTHS:
        path = monthly_file_path(data_root, SYMBOL, "M1", month)
        monthly_data = load_month(
            data_root,
            SYMBOL,
            "M1",
            month,
            manifest=manifest,
        )
        for line_number, candle in enumerate(monthly_data.candles, start=2):
            raw_item = next(raw_rows, None)
            if raw_item is None:
                raise MonthlyDataError(
                    f"{path}:{line_number}: monthly M1 has an extra row after the raw sequence ended."
                )
            raw_row, raw_path, raw_line = raw_item
            monthly_time = str(candle["time"])
            if monthly_time[:7] != month:
                raise MonthlyDataError(
                    f"{path}:{line_number}: bar open time belongs to {monthly_time[:7]}, not {month}."
                )
            if monthly_time <= previous_monthly_time:
                raise MonthlyDataError(
                    f"{path}:{line_number}: duplicate or out-of-order monthly bar at a month boundary."
                )
            monthly_prices = tuple(
                candle[name] for name in ("open", "high", "low", "close")
            )
            raw_prices = tuple(
                Decimal(raw_row[name]) for name in ("open", "high", "low", "close")
            )
            if monthly_time != raw_row["time"] or monthly_prices != raw_prices:
                price_deltas = [
                    abs(monthly - raw)
                    for monthly, raw in zip(monthly_prices, raw_prices)
                ]
                raise MonthlyDataError(
                    f"{path}:{line_number}: row {row_count + 1} differs from "
                    f"{raw_path}:{raw_line}; monthly time={monthly_time}, "
                    f"raw time={raw_row['time']}, price deltas={price_deltas}."
                )
            normalized_prices = [
                format(value.normalize(), "f") for value in raw_prices
            ]
            digest.update(
                ("\x1f".join((raw_row["time"], *normalized_prices)) + "\n").encode(
                    "utf-8"
                )
            )
            first_time = first_time or raw_row["time"]
            last_time = raw_row["time"]
            previous_monthly_time = monthly_time
            row_count += 1

    raw_extra = next(raw_rows, None)
    if raw_extra is not None:
        raw_row, raw_path, raw_line = raw_extra
        raise MonthlyDataError(
            f"{raw_path}:{raw_line}: raw archive has a row after the monthly M1 sequence ended "
            f"({raw_row['time']})."
        )
    if row_count != EXPECTED_MERGED_ROWS:
        raise MonthlyDataError(
            f"Expected {EXPECTED_MERGED_ROWS:,} data-matched M1 rows, found {row_count:,}."
        )
    if (
        first_time is None
        or first_time[:10] != EXPECTED_START_DATE
        or last_time is None
        or last_time[:10] != EXPECTED_END_DATE
    ):
        raise MonthlyDataError(
            f"Monthly M1 span must be {EXPECTED_START_DATE} through {EXPECTED_END_DATE}; "
            f"found {first_time} through {last_time}."
        )
    return digest.hexdigest()


def verify_monthly_file_hashes(
    data_root: Path,
    *,
    manifest: dict[str, Any] | None = None,
) -> None:
    """Verify each monthly CSV against the owner-approved manifest."""
    manifest = manifest or load_monthly_manifest(data_root)
    timeframes = manifest.get("timeframes")
    if not isinstance(timeframes, dict):
        raise MonthlyDataError("Monthly manifest is missing timeframe records.")
    for timeframe, month_records in timeframes.items():
        if not isinstance(month_records, dict):
            raise MonthlyDataError(
                f"Monthly manifest has invalid {timeframe} records."
            )
        for month, record in month_records.items():
            if not isinstance(record, dict):
                raise MonthlyDataError(
                    f"Monthly manifest has an invalid {timeframe} record for {month}."
                )
            path = monthly_file_path(data_root, SYMBOL, timeframe, month)
            try:
                actual_hash = sha256_file(path)
            except OSError as exc:
                raise MonthlyDataError(
                    f"{path}: monthly file is missing or unreadable: {exc}"
                ) from exc
            if actual_hash != record.get("sha256"):
                raise MonthlyDataError(
                    f"{path}: file hash differs from the monthly manifest."
                )


def _atomic_csv(path: Path, rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=["time", "open", "high", "low", "close", "source"],
                lineterminator="\r\n",
            )
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _raw_archive_hashes(data_root: Path) -> dict[str, str]:
    archive_hashes: dict[str, str] = {}
    for relative_path in RAW_ARCHIVES:
        path = data_root / relative_path
        if not path.is_file():
            raise MonthlyDataError(f"Required raw archive is missing: {path}.")
        archive_hashes[relative_path.as_posix()] = sha256_file(path)
    return archive_hashes


def _iter_raw_archives(
    data_root: Path,
) -> Iterable[tuple[dict[str, str], Path, int]]:
    previous_time: datetime | None = None
    first_time: datetime | None = None
    last_time: datetime | None = None
    row_count = 0
    for relative_path in RAW_ARCHIVES:
        path = data_root / relative_path
        if not path.is_file():
            raise MonthlyDataError(f"Required raw archive is missing: {path}.")
        expected_year = path.name.split("_")[-2]
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            headers = {
                (name or "").strip().lower(): name for name in (reader.fieldnames or [])
            }
            required = {"timestamp_utc", "open", "high", "low", "close"}
            if required - headers.keys():
                raise MonthlyDataError(f"{path}: required OHLC timestamp columns are missing.")
            for line_number, row in enumerate(reader, start=2):
                raw_time = (row.get(headers["timestamp_utc"]) or "").strip()
                try:
                    timestamp = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError as exc:
                    raise MonthlyDataError(
                        f"{path}:{line_number}: invalid UTC minute {raw_time!r}."
                    ) from exc
                if timestamp.year != int(expected_year):
                    raise MonthlyDataError(
                        f"{path}:{line_number}: timestamp is outside {expected_year}."
                    )
                if timestamp.second or timestamp.microsecond:
                    raise MonthlyDataError(
                        f"{path}:{line_number}: source timestamps must be on whole minutes."
                    )
                if previous_time is not None and timestamp <= previous_time:
                    problem = "duplicate" if timestamp == previous_time else "out of order"
                    raise MonthlyDataError(
                        f"{path}:{line_number}: raw source rows are {problem}."
                    )
                prices = tuple(
                    (row.get(headers[name]) or "").strip()
                    for name in ("open", "high", "low", "close")
                )
                if not all(prices):
                    raise MonthlyDataError(f"{path}:{line_number}: OHLC value is empty.")
                try:
                    numeric_prices = tuple(Decimal(value) for value in prices)
                except InvalidOperation as exc:
                    raise MonthlyDataError(
                        f"{path}:{line_number}: OHLC value is not numeric."
                    ) from exc
                if any(not value.is_finite() for value in numeric_prices):
                    raise MonthlyDataError(
                        f"{path}:{line_number}: OHLC values must be finite."
                    )
                normalized_time = format_utc_timestamp(timestamp)
                first_time = first_time or timestamp
                previous_time = timestamp
                last_time = timestamp
                row_count += 1
                yield (
                    {
                        "time": normalized_time,
                        "open": prices[0],
                        "high": prices[1],
                        "low": prices[2],
                        "close": prices[3],
                        "source": SOURCE,
                    },
                    path,
                    line_number,
                )
    if row_count != EXPECTED_MERGED_ROWS:
        raise MonthlyDataError(
            f"Expected {EXPECTED_MERGED_ROWS:,} raw rows, found {row_count:,}."
        )
    if (
        first_time is None
        or first_time.date().isoformat() != EXPECTED_START_DATE
        or last_time is None
        or last_time.date().isoformat() != EXPECTED_END_DATE
    ):
        raise MonthlyDataError(
            f"Raw archives must span {EXPECTED_START_DATE} through {EXPECTED_END_DATE}; "
            f"found {first_time} through {last_time}."
        )


def _read_raw_archives(data_root: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    archive_hashes = _raw_archive_hashes(data_root)
    rows = [row for row, _path, _line in _iter_raw_archives(data_root)]
    return rows, archive_hashes


def _fingerprint_rows(rows: Iterable[dict[str, str]]) -> str:
    """Return a numeric-data fingerprint independent of CSV serialization."""
    digest = hashlib.sha256()
    for row in rows:
        prices = [
            format(Decimal(row[name]).normalize(), "f")
            for name in ("open", "high", "low", "close")
        ]
        digest.update(("\x1f".join((row["time"], *prices)) + "\n").encode("utf-8"))
    return digest.hexdigest()


def build_xau_monthly_dataset(
    data_root: Path = DEFAULT_DATA_DIR,
) -> MonthlyBuildResult:
    """Verify the raw-archive gate, then write 36 M1 and 180 derived files."""
    rows, archive_hashes = _read_raw_archives(data_root)
    merged_sha256 = _fingerprint_rows(rows)
    month_rows: dict[str, list[dict[str, str]]] = {month: [] for month in MONTHS}
    for row in rows:
        month = row["time"][:7]
        if month not in month_rows:
            raise MonthlyDataError(f"Raw archive contains an unsupported month: {month}.")
        month_rows[month].append(row)
    if any(not month_rows[month] for month in MONTHS):
        raise MonthlyDataError("Every calendar month from 2023-01 through 2025-12 is required.")

    month_counts: dict[str, dict[str, int]] = {timeframe: {} for timeframe in TIMEFRAMES}
    month_hashes: dict[str, dict[str, str]] = {timeframe: {} for timeframe in TIMEFRAMES}
    for month in MONTHS:
        m1_path = monthly_file_path(data_root, SYMBOL, "M1", month)
        first_time = month_rows[month][0]["time"]
        if first_time[:7] != month:
            raise MonthlyDataError(f"{month} M1 file does not start in its own month.")
        _atomic_csv(m1_path, month_rows[month])
        m1_data = load_candle_file(m1_path)
        if str(m1_data.candles[0]["time"]) != first_time:
            raise MonthlyDataError(f"{month} M1 file does not start at its first trading minute.")
        month_counts["M1"][month] = len(m1_data.candles)
        month_hashes["M1"][month] = m1_data.sha256
        for timeframe, interval in INTERVALS.items():
            candles, _incomplete = _aggregate(m1_data.candles, interval)
            if not candles:
                raise MonthlyDataError(f"{month} produced no complete {timeframe} candles.")
            if any(str(candle["time"])[:7] != month for candle in candles):
                raise MonthlyDataError(f"{month} {timeframe} output crossed a month boundary.")
            expected_first = format_utc_timestamp(
                _utc_bucket_start(
                    m1_data.candles[0]["timestamp"],
                    interval,
                )
            )
            if str(candles[0]["time"]) != expected_first:
                raise MonthlyDataError(
                    f"{month} {timeframe} does not start at the month's first trading bucket."
                )
            derived_path = monthly_file_path(data_root, SYMBOL, timeframe, month)
            digest = _write_candles(derived_path, candles, has_source=True)
            month_counts[timeframe][month] = len(candles)
            month_hashes[timeframe][month] = digest

    manifest = {
        "schema_version": 1,
        "symbol": SYMBOL,
        "source": SOURCE,
        "status": "OWNER_APPROVED",
        "owner_approved": True,
        "source_sha256": merged_sha256,
        "source_rows": len(rows),
        "source_raw_archives": archive_hashes,
        "approval_basis": "raw archive row-by-row data fidelity",
        "months": list(MONTHS),
        "timeframes": {
            timeframe: {
                month: {
                    "row_count": month_counts[timeframe][month],
                    "sha256": month_hashes[timeframe][month],
                    **(
                        {"m1_sha256": month_hashes["M1"][month]}
                        if timeframe != "M1"
                        else {}
                    ),
                }
                for month in MONTHS
            }
            for timeframe in TIMEFRAMES
        },
    }
    manifest_path = monthly_manifest_path(data_root, SYMBOL)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{manifest_path.name}.", suffix=".tmp", dir=manifest_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
        os.replace(temporary_path, manifest_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    verified_manifest = load_monthly_manifest(data_root, symbol=SYMBOL)
    verify_monthly_gate(data_root, manifest=verified_manifest)
    return MonthlyBuildResult(
        merged_sha256=merged_sha256,
        merged_rows=len(rows),
        month_counts=month_counts,
        month_hashes=month_hashes,
        raw_archive_hashes=archive_hashes,
    )


def regenerate_monthly_outputs(
    data_root: Path = DEFAULT_DATA_DIR,
    *,
    output_root: Path | None = None,
) -> dict[str, Any]:
    """Regenerate all monthly CSVs from raw archives without rewriting the manifest."""
    output_root = output_root or data_root
    manifest = load_monthly_manifest(data_root)
    source_fingerprint = verify_monthly_gate(data_root, manifest=manifest)
    rows, archive_hashes = _read_raw_archives(data_root)
    if archive_hashes != manifest["source_raw_archives"]:
        raise MonthlyDataError("Raw archive hashes changed during monthly regeneration.")

    month_rows: dict[str, list[dict[str, str]]] = {month: [] for month in MONTHS}
    for row in rows:
        month = row["time"][:7]
        if month not in month_rows:
            raise MonthlyDataError(f"Raw archive contains an unsupported month: {month}.")
        month_rows[month].append(row)

    staged_files: list[tuple[Path, Path, str]] = []
    with tempfile.TemporaryDirectory(prefix="xauusd-monthly-regeneration-") as temp_dir:
        stage_root = Path(temp_dir)
        for month in MONTHS:
            raw_month = month_rows[month]
            if not raw_month:
                raise MonthlyDataError(f"Raw archives contain no rows for {month}.")
            m1_stage = monthly_file_path(stage_root, SYMBOL, "M1", month)
            _atomic_csv(m1_stage, raw_month)
            m1_record = manifest["timeframes"]["M1"][month]
            m1_hash = sha256_file(m1_stage)
            if m1_hash != m1_record["sha256"] or len(raw_month) != m1_record["row_count"]:
                raise MonthlyDataError(
                    f"{month} regenerated M1 does not match the frozen manifest."
                )
            staged_files.append(
                (
                    m1_stage,
                    monthly_file_path(output_root, SYMBOL, "M1", month),
                    m1_record["sha256"],
                )
            )

            m1_data = load_candle_file(m1_stage)
            for timeframe, interval in INTERVALS.items():
                candles, _incomplete = _aggregate(m1_data.candles, interval)
                if not candles or any(str(candle["time"])[:7] != month for candle in candles):
                    raise MonthlyDataError(
                        f"{month} regenerated {timeframe} contains no valid month-only bars."
                    )
                record = manifest["timeframes"][timeframe][month]
                if len(candles) != record["row_count"]:
                    raise MonthlyDataError(
                        f"{month} regenerated {timeframe} row count differs from the manifest."
                    )
                derived_stage = monthly_file_path(stage_root, SYMBOL, timeframe, month)
                digest = _write_candles(derived_stage, candles, has_source=True)
                if digest != record["sha256"]:
                    raise MonthlyDataError(
                        f"{month} regenerated {timeframe} hash differs from the manifest."
                    )
                staged_files.append(
                    (
                        derived_stage,
                        monthly_file_path(output_root, SYMBOL, timeframe, month),
                        record["sha256"],
                    )
                )

        files_written = 0
        files_unchanged = 0
        for staged_path, target_path, expected_hash in staged_files:
            if target_path.is_file() and sha256_file(target_path) == expected_hash:
                files_unchanged += 1
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                dir=target_path.parent,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as destination, staged_path.open(
                    "rb"
                ) as source:
                    shutil.copyfileobj(source, destination)
                os.replace(temporary_path, target_path)
            finally:
                temporary_path.unlink(missing_ok=True)
            files_written += 1

    return {
        "symbol": SYMBOL,
        "source_sha256": source_fingerprint,
        "month_count": len(MONTHS),
        "timeframe_counts": {
            timeframe: len(manifest["timeframes"][timeframe])
            for timeframe in TIMEFRAMES
        },
        "files_written": files_written,
        "files_unchanged": files_unchanged,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build verified monthly XAUUSD M1 and higher-timeframe files."
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args(argv)
    try:
        result = build_xau_monthly_dataset(args.data_dir)
    except (OSError, MonthlyDataError, ValueError) as exc:
        print(f"Monthly data build stopped: {exc}", file=sys.stderr)
        return 2
    print(
        f"{SYMBOL} gate verified: {result.merged_sha256} "
        f"({result.merged_rows:,} rows)"
    )
    for month in MONTHS:
        counts = " · ".join(
            f"{timeframe} {result.month_counts[timeframe][month]:,}"
            for timeframe in TIMEFRAMES
        )
        print(f"{month}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
