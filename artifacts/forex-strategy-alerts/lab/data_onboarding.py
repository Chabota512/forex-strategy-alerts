"""Normalize downloaded yearly M1 files and write local-only review manifests."""

from __future__ import annotations

import argparse
import csv
import heapq
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from .candle_data import (
    DEFAULT_DATA_DIR,
    format_utc_timestamp,
    load_candle_file,
    parse_timestamp,
    sha256_file,
)
from .engine import Candle, CandleDataError
from .instruments import INSTRUMENT_REGISTRY

REQUIRED_YEARS = (2023, 2024, 2025)
REQUIRED_YEARS_BY_SYMBOL: dict[str, tuple[int, ...]] = {
    "XAUUSD": (2023, 2024, 2025),
    "GBPUSD": (2023, 2024, 2025),
    "USDJPY": (2023, 2024, 2025),
    "GBPJPY": (2023, 2024, 2025),
    "AUDJPY": (2024, 2025),
}
SOURCE_FILENAME = re.compile(
    r"^(?P<symbol>[A-Z0-9]+)_M1_BID_(?P<year>20\d{2})_UTC\.csv$",
    re.IGNORECASE,
)
TIME_ALIASES = ("time", "timestamp", "timestamp_utc", "datetime", "date")
PRICE_COLUMNS = ("open", "high", "low", "close")
SANITY_DIRNAME = "sanity_checks"
ARCHIVE_DIRNAME = "historicalMarketData"
PENDING_STATUS = "PENDING_OWNER_SPOTCHECK"
OWNER_SPOTCHECK_METHOD = "OWNER_SPOTCHECK"


@dataclass
class LocalAudit:
    row_count: int = 0
    rows_by_year: Counter[int] = field(default_factory=Counter)
    months_by_year: dict[int, set[str]] = field(default_factory=dict)
    weekend_rows: int = 0
    gap_count: int = 0
    missing_minutes_across_gaps: int = 0
    max_missing_minutes: int = 0
    largest_gaps: list[dict[str, Any]] = field(default_factory=list)
    start_utc: str | None = None
    end_utc: str | None = None
    chronological: bool = True
    ohlc_valid: bool = True

    def add(self, timestamp: datetime) -> None:
        value = format_utc_timestamp(timestamp)
        self.row_count += 1
        self.rows_by_year[timestamp.year] += 1
        self.months_by_year.setdefault(timestamp.year, set()).add(value[:7])
        self.weekend_rows += int(timestamp.weekday() >= 5)
        if self.start_utc is None:
            self.start_utc = value
        self.end_utc = value

    def add_gap(self, previous: datetime, current: datetime) -> None:
        minutes = int((current - previous).total_seconds() // 60) - 1
        if minutes <= 0:
            return
        self.gap_count += 1
        self.missing_minutes_across_gaps += minutes
        self.max_missing_minutes = max(self.max_missing_minutes, minutes)
        self.largest_gaps.append(
            {
                "from_utc": format_utc_timestamp(previous),
                "to_utc": format_utc_timestamp(current),
                "missing_minutes": minutes,
            }
        )
        self.largest_gaps.sort(
            key=lambda gap: (-int(gap["missing_minutes"]), str(gap["from_utc"]))
        )
        del self.largest_gaps[10:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS" if self.row_count and self.chronological and self.ohlc_valid else "FAIL",
            "row_count": self.row_count,
            "row_counts_by_year": {
                str(year): self.rows_by_year[year]
                for year in sorted(self.rows_by_year)
            },
            "months_by_year": {
                str(year): sorted(months)
                for year, months in sorted(self.months_by_year.items())
            },
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "chronological": self.chronological,
            "ohlc_valid": self.ohlc_valid,
            "weekend_rows": self.weekend_rows,
            "gaps_over_one_minute": self.gap_count,
            "missing_minutes_across_gaps": self.missing_minutes_across_gaps,
            "largest_gap_minutes": self.max_missing_minutes,
            "largest_gaps": self.largest_gaps,
        }


@dataclass
class SourceFile:
    path: Path
    symbol: str
    named_year: int
    sha256: str
    audit: LocalAudit = field(default_factory=LocalAudit)
    source_labels: set[str] = field(default_factory=set)
    error: str | None = None

    def to_dict(self, data_dir: Path) -> dict[str, Any]:
        local_integrity = self.audit.to_dict()
        if self.error:
            local_integrity["status"] = "FAIL"
        return {
            "file": str(self.path.relative_to(data_dir)),
            "named_year": self.named_year,
            "sha256": self.sha256,
            "local_integrity": local_integrity,
            "source_labels": sorted(self.source_labels),
            "error": self.error,
        }


def _source_files(data_dir: Path, symbol: str) -> tuple[list[SourceFile], list[str]]:
    files_by_year: dict[int, list[SourceFile]] = {}
    errors: list[str] = []
    for path in sorted(data_dir.rglob("*_M1_BID_20*_UTC.csv")):
        if not path.is_file():
            continue
        match = SOURCE_FILENAME.fullmatch(path.name)
        if match is None or match.group("symbol").upper() != symbol:
            continue
        named_year = int(match.group("year"))
        source = SourceFile(
            path=path,
            symbol=symbol,
            named_year=named_year,
            sha256=sha256_file(path),
        )
        files_by_year.setdefault(named_year, []).append(source)

    selected: list[SourceFile] = []
    for year, year_files in sorted(files_by_year.items()):
        hashes = {source.sha256 for source in year_files}
        if len(hashes) > 1:
            errors.append(
                f"{symbol} {year}: multiple different yearly source files are present."
            )
            continue
        selected.append(year_files[0])
    return selected, errors


def _iter_source_rows_unchecked(source: SourceFile) -> Iterator[dict[str, Any]]:
    previous: datetime | None = None
    with source.path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        headers = {
            (name or "").strip().lower(): name
            for name in (reader.fieldnames or [])
            if name is not None
        }
        time_column = next(
            (headers[name] for name in TIME_ALIASES if name in headers), None
        )
        missing = set(PRICE_COLUMNS) - headers.keys()
        if time_column is None:
            missing.add("one timestamp column")
        if missing:
            raise ValueError(
                f"{source.path}: missing columns: {', '.join(sorted(missing))}"
            )
        source_column = headers.get("source")

        for line_number, row in enumerate(reader, start=2):
            raw_time = (row.get(time_column) or "").strip()
            if not raw_time:
                raise ValueError(f"{source.path}:{line_number}: timestamp is empty")
            timestamp = parse_timestamp(raw_time, source.path, line_number)
            if timestamp.second or timestamp.microsecond:
                raise ValueError(
                    f"{source.path}:{line_number}: M1 time must be on a whole-minute boundary"
                )
            if previous is not None:
                if timestamp <= previous:
                    raise ValueError(
                        f"{source.path}:{line_number}: timestamps must be strictly increasing"
                    )
                source.audit.add_gap(previous, timestamp)
            previous = timestamp

            if abs(timestamp.year - source.named_year) > 1:
                raise ValueError(
                    f"{source.path}:{line_number}: timestamp year is outside the named "
                    "year and its adjacent boundary years"
                )
            try:
                candle = Candle.from_values(
                    format_utc_timestamp(timestamp),
                    *(row.get(headers[column]) or "" for column in PRICE_COLUMNS),
                )
            except CandleDataError as exc:
                raise ValueError(f"{source.path}:{line_number}: {exc}") from exc

            source_label = (
                (row.get(source_column) or "").strip().lower()
                if source_column
                else (
                    "dukascopy"
                    if SOURCE_FILENAME.fullmatch(source.path.name)
                    else "unverified_real"
                )
            )
            if not source_label:
                raise ValueError(f"{source.path}:{line_number}: source is empty")
            source.source_labels.add(source_label)
            if len(source.source_labels) > 1:
                raise ValueError(f"{source.path}: source labels must be consistent")
            source.audit.add(timestamp)
            yield {
                "timestamp": timestamp,
                "time": format_utc_timestamp(timestamp),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "source": source_label,
            }

    if source.audit.row_count == 0:
        raise ValueError(f"{source.path}: no M1 candles found")


def _iter_source_rows(source: SourceFile) -> Iterator[dict[str, Any]]:
    try:
        yield from _iter_source_rows_unchecked(source)
    except (OSError, ValueError) as exc:
        source.error = str(exc)
        raise


def _audit_loaded_file(path: Path) -> LocalAudit:
    data = load_candle_file(path)
    audit = LocalAudit()
    previous: datetime | None = None
    for candle in data.candles:
        timestamp = candle["timestamp"]
        if previous is not None:
            audit.add_gap(previous, timestamp)
        audit.add(timestamp)
        previous = timestamp
    return audit


def _expected_months(years: Sequence[int]) -> set[str]:
    return {
        f"{year:04d}-{month:02d}"
        for year in years
        for month in range(1, 13)
    }


def _copy_archives(sources: Sequence[SourceFile], data_dir: Path) -> None:
    for source in sources:
        destination = (
            data_dir
            / ARCHIVE_DIRNAME
            / source.symbol
            / source.path.name
        )
        if source.path.resolve() == destination.resolve():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if sha256_file(destination) != source.sha256:
                raise ValueError(
                    f"Refusing to overwrite different archived data at {destination}."
                )
        else:
            shutil.copy2(source.path, destination)


def _merge_sources(
    sources: Sequence[SourceFile],
    destination: Path,
) -> tuple[LocalAudit, int, str]:
    iterators = [_iter_source_rows(source) for source in sources]
    merged_audit = LocalAudit()
    duplicate_rows = 0
    previous: dict[str, Any] | None = None
    merged_source: str | None = None
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("time", "open", "high", "low", "close", "source"),
        )
        writer.writeheader()
        for row in heapq.merge(*iterators, key=lambda item: item["timestamp"]):
            if merged_source is None:
                merged_source = str(row["source"])
            elif row["source"] != merged_source:
                raise ValueError(
                    "Yearly inputs have different source labels; refusing to mix them."
                )
            if previous is not None and row["timestamp"] == previous["timestamp"]:
                if any(row[key] != previous[key] for key in PRICE_COLUMNS):
                    raise ValueError(
                        "Conflicting OHLC values exist at overlapping timestamp "
                        f"{row['time']}."
                    )
                duplicate_rows += 1
                continue
            if previous is not None and row["timestamp"] < previous["timestamp"]:
                raise ValueError("Merged source timestamps are not chronological.")
            if previous is not None:
                merged_audit.add_gap(previous["timestamp"], row["timestamp"])
            merged_audit.add(row["timestamp"])
            writer.writerow(
                {
                    "time": row["time"],
                    "open": format(row["open"], "f"),
                    "high": format(row["high"], "f"),
                    "low": format(row["low"], "f"),
                    "close": format(row["close"], "f"),
                    "source": row["source"],
                }
            )
            previous = row
    if merged_source is None:
        raise ValueError("No valid input rows were found.")
    return merged_audit, duplicate_rows, merged_source


def _manifest_path(data_dir: Path, symbol: str) -> Path:
    return data_dir / SANITY_DIRNAME / f"{symbol}_M1.json"


def onboard_symbol(
    symbol: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    years: Sequence[int] | None = None,
) -> dict[str, Any]:
    symbol = symbol.strip().upper()
    if symbol not in INSTRUMENT_REGISTRY:
        raise ValueError(f"Instrument {symbol} is not registered.")
    if symbol == "XAUUSD" and data_dir.resolve() == DEFAULT_DATA_DIR.resolve():
        raise ValueError(
            "XAUUSD uses the tracked verified monthly dataset; root-level M1 onboarding is disabled."
        )
    configured_years = REQUIRED_YEARS_BY_SYMBOL[symbol]
    if years is not None and tuple(years) != configured_years:
        raise ValueError(
            f"Onboarding {symbol} requires the configured years {configured_years}."
        )
    years = configured_years

    sources, source_errors = _source_files(data_dir, symbol)
    required_sources = [
        source for source in sources if source.named_year in years
    ]
    unrequested_sources = [
        source for source in sources if source.named_year not in years
    ]
    source_years = {source.named_year for source in sources}
    required_source_years = {source.named_year for source in required_sources}
    missing_years = [year for year in years if year not in required_source_years]
    unrequested_source_errors: list[str] = []
    for source in unrequested_sources:
        try:
            for _row in _iter_source_rows(source):
                pass
        except (OSError, ValueError) as exc:
            unrequested_source_errors.append(str(exc))
    source_rows_by_year: Counter[int] = Counter()

    primary_path = data_dir / f"{symbol}_M1.csv"
    temporary_path: Path | None = None
    merged_audit: LocalAudit | None = None
    duplicate_rows = 0
    merge_source: str | None = None
    merge_error: str | None = None
    input_months: set[str] = set()

    if required_sources and not source_errors:
        data_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{symbol}_M1.", suffix=".tmp", dir=data_dir
        )
        os.close(fd)
        Path(temp_name).unlink(missing_ok=True)
        temporary_path = Path(temp_name)
        try:
            merged_audit, duplicate_rows, merge_source = _merge_sources(
                required_sources,
                temporary_path,
            )
            source_rows_by_year.update(merged_audit.rows_by_year)
            input_months.update(
                month
                for months in merged_audit.months_by_year.values()
                for month in months
            )
        except (OSError, ValueError) as exc:
            merge_error = str(exc)
            temporary_path.unlink(missing_ok=True)

    source_complete = (
        not source_errors
        and merge_error is None
        and not missing_years
        and input_months == _expected_months(years)
    )
    if source_complete and temporary_path is not None:
        _copy_archives(sources, data_dir)
        temporary_path.replace(primary_path)
        temporary_path = None
    else:
        _copy_archives(sources, data_dir)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    primary_exists = primary_path.is_file()
    primary_sha256 = sha256_file(primary_path) if primary_exists else None
    if primary_exists:
        try:
            primary_audit = _audit_loaded_file(primary_path)
        except (OSError, ValueError) as exc:
            primary_audit = None
            source_errors.append(f"Canonical {primary_path.name}: {exc}")
    elif merged_audit is not None:
        primary_audit = merged_audit
    else:
        primary_audit = None

    if not source_rows_by_year and primary_audit is not None:
        source_rows_by_year.update(primary_audit.rows_by_year)

    expected_months = _expected_months(years)
    missing_months = sorted(expected_months - input_months)
    if not required_sources and primary_audit is not None:
        canonical_months = {
            month
            for months in primary_audit.months_by_year.values()
            for month in months
        }
        missing_months = sorted(expected_months - canonical_months)
    local_integrity = (
        primary_audit.to_dict()
        if primary_audit is not None
        else {
            "status": "MISSING",
            "row_count": 0,
            "row_counts_by_year": {},
            "months_by_year": {},
            "start_utc": None,
            "end_utc": None,
            "chronological": None,
            "ohlc_valid": None,
            "weekend_rows": None,
            "gaps_over_one_minute": None,
            "missing_minutes_across_gaps": None,
            "largest_gap_minutes": None,
            "largest_gaps": [],
        }
    )

    data_complete = source_complete and primary_exists and not source_errors
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": PENDING_STATUS,
        "owner_approved": False,
        "review_method": OWNER_SPOTCHECK_METHOD,
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "symbol": symbol,
        "timeframe": "M1",
        "required_years": list(years),
        "available_source_years": sorted(source_years),
        "missing_years": missing_years,
        "missing_months": missing_months,
        "data_complete": data_complete,
        "year_rows": {
            str(year): int(source_rows_by_year.get(year, 0))
            for year in years
        },
        "primary_file": str(primary_path.relative_to(data_dir))
        if primary_exists
        else None,
        "primary_sha256": primary_sha256,
        "source_files": [
            source.to_dict(data_dir) for source in required_sources
        ],
        "unrequested_source_years": sorted(
            {source.named_year for source in unrequested_sources}
        ),
        "unrequested_source_files": [
            source.to_dict(data_dir) for source in unrequested_sources
        ],
        "unrequested_source_errors": unrequested_source_errors,
        "local_integrity": local_integrity,
        "overlap_rows_deduplicated": duplicate_rows,
        "errors": source_errors + ([merge_error] if merge_error else []),
        "review_note": (
            "Local integrity only: no external reference was queried or compared. "
            "The owner must independently spot-check closes before approval."
        ),
    }
    manifest_path = _manifest_path(data_dir, symbol)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def onboard_all(
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    years: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    return [
        onboard_symbol(symbol, data_dir=data_dir, years=years)
        for symbol in INSTRUMENT_REGISTRY
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Onboard yearly local M1 files, run local integrity checks, and leave "
            "owner spot-check manifests pending."
        )
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--symbols",
        nargs="*",
        type=str.upper,
        choices=tuple(INSTRUMENT_REGISTRY),
    )
    args = parser.parse_args(argv)
    symbols = args.symbols or tuple(INSTRUMENT_REGISTRY)
    try:
        manifests = [
            onboard_symbol(symbol, data_dir=args.data_dir)
            for symbol in symbols
        ]
    except (OSError, ValueError) as exc:
        print(f"Data onboarding stopped: {exc}", file=sys.stderr)
        return 2

    for manifest in manifests:
        print(
            f"{manifest['symbol']}: {manifest['status']}; "
            f"rows={manifest['year_rows']}; "
            f"missing_years={manifest['missing_years']}; "
            f"complete={manifest['data_complete']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
