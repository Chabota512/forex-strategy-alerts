"""Materialize complete UTC higher-timeframe candles from approved M1 data."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

if __package__:
    from .candle_data import (
        DEFAULT_DATA_DIR,
        LAB_DIR,
        format_utc_timestamp,
        parse_timestamp,
        sha256_file,
    )
    from .data_sanity import require_owner_approved_sanity
    from .engine import Candle, CandleDataError
else:
    from candle_data import (
        DEFAULT_DATA_DIR,
        LAB_DIR,
        format_utc_timestamp,
        parse_timestamp,
        sha256_file,
    )
    from data_sanity import require_owner_approved_sanity
    from engine import Candle, CandleDataError

REQUIRED_COLUMNS = {"time", "open", "high", "low", "close"}
SYMBOL_FILENAME = re.compile(r"^([A-Za-z0-9]+)_M1$", re.IGNORECASE)
INTERVALS = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
DEFAULT_OUTPUT_DIR = DEFAULT_DATA_DIR
PROVENANCE_SCHEMA_VERSION = 1


def _utc_bucket_start(timestamp: datetime, interval_minutes: int) -> datetime:
    utc_timestamp = timestamp.astimezone(timezone.utc)
    minute_of_day = utc_timestamp.hour * 60 + utc_timestamp.minute
    start_minute = minute_of_day - minute_of_day % interval_minutes
    return utc_timestamp.replace(
        hour=start_minute // 60,
        minute=start_minute % 60,
        second=0,
        microsecond=0,
    )


def _read_m1_file(path: Path) -> tuple[str, bool, list[dict[str, Any]]]:
    match = SYMBOL_FILENAME.fullmatch(path.stem)
    if not match:
        raise ValueError(
            f"{path.name}: expected a SYMBOL_M1.csv filename, such as GBPUSD_M1.csv"
        )

    rows: list[dict[str, Any]] = []
    previous_timestamp: datetime | None = None
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")
        has_source = "source" in columns
        source_labels: set[str] = set()
        source_values: set[str] = set()

        for line_number, row in enumerate(reader, start=2):
            raw_time = (row.get("time") or "").strip()
            if not raw_time:
                raise ValueError(f"{path}:{line_number}: time is empty")
            timestamp = parse_timestamp(raw_time, path, line_number)
            if timestamp.second != 0 or timestamp.microsecond != 0:
                raise ValueError(
                    f"{path}:{line_number}: M1 timestamps must be on whole-minute boundaries"
                )
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError(
                    f"{path}:{line_number}: timestamps must be strictly increasing"
                )
            previous_timestamp = timestamp
            try:
                candle = Candle.from_values(
                    format_utc_timestamp(timestamp),
                    row.get("open") or "",
                    row.get("high") or "",
                    row.get("low") or "",
                    row.get("close") or "",
                )
            except CandleDataError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc

            source = (row.get("source") or "").strip()
            if has_source:
                if not source:
                    raise ValueError(f"{path}:{line_number}: source is empty")
                source_labels.add(source.lower())
                source_values.add(source)
            rows.append(
                {
                    "timestamp": timestamp,
                    "time": format_utc_timestamp(timestamp),
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "source": source,
                }
            )

    if not rows:
        raise ValueError(f"{path}: no M1 candles found")
    if len(source_labels) > 1:
        raise ValueError(f"{path}: source labels must be consistent")

    source_value = next(iter(source_values)) if source_values else ""
    return match.group(1).upper(), has_source, rows


def _aggregate(
    rows: list[dict[str, Any]],
    interval_minutes: int,
) -> tuple[list[dict[str, Any]], int]:
    groups: dict[datetime, list[dict[str, Any]]] = {}
    for row in rows:
        start = _utc_bucket_start(row["timestamp"], interval_minutes)
        groups.setdefault(start, []).append(row)

    output: list[dict[str, Any]] = []
    incomplete_buckets = 0
    for start, group in sorted(groups.items()):
        expected = [
            start + timedelta(minutes=offset)
            for offset in range(interval_minutes)
        ]
        if [row["timestamp"] for row in group] != expected:
            incomplete_buckets += 1
            continue
        output.append(
            {
                "time": format_utc_timestamp(start),
                "open": group[0]["open"],
                "high": max(row["high"] for row in group),
                "low": min(row["low"] for row in group),
                "close": group[-1]["close"],
                "source": group[0]["source"],
            }
        )
    return output, incomplete_buckets


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _write_candles(
    path: Path,
    candles: list[dict[str, Any]],
    *,
    has_source: bool,
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["time", "open", "high", "low", "close"]
    if has_source:
        fieldnames.append("source")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            for candle in candles:
                output_row = {
                    "time": candle["time"],
                    "open": _decimal_text(candle["open"]),
                    "high": _decimal_text(candle["high"]),
                    "low": _decimal_text(candle["low"]),
                    "close": _decimal_text(candle["close"]),
                }
                if has_source:
                    output_row["source"] = candle["source"]
                writer.writerow(output_row)

        new_hash = sha256_file(temporary_path)
        if path.is_file() and sha256_file(path) == new_hash:
            temporary_path.unlink()
        else:
            os.replace(temporary_path, path)
        return new_hash
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_provenance(
    output_dir: Path,
    *,
    symbol: str,
    m1_sha256: str,
    m1_rows: int,
    outputs: dict[str, dict[str, Any]],
) -> Path:
    provenance_path = output_dir / "provenance" / f"{symbol}.json"
    timeframe_records = {
        timeframe: {
            "row_count": int(output["bars_written"]),
            "sha256": str(output["sha256"]),
            "incomplete_buckets_omitted": int(
                output["incomplete_buckets_omitted"]
            ),
        }
        for timeframe, output in outputs.items()
    }
    previous: dict[str, Any] = {}
    if provenance_path.is_file():
        try:
            loaded = json.loads(provenance_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, json.JSONDecodeError):
            previous = {}

    same_generation = (
        previous.get("symbol") == symbol
        and previous.get("m1_sha256") == m1_sha256
        and previous.get("m1_rows") == m1_rows
        and previous.get("timeframes") == timeframe_records
        and isinstance(previous.get("generated_at_utc"), str)
    )
    generated_at = (
        previous["generated_at_utc"]
        if same_generation
        else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    provenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "symbol": symbol,
        "m1_sha256": m1_sha256,
        "m1_rows": m1_rows,
        "timeframes": timeframe_records,
        "generated_at_utc": generated_at,
    }
    serialized = json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    if (
        provenance_path.is_file()
        and provenance_path.read_text(encoding="utf-8") == serialized
    ):
        return provenance_path
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{provenance_path.name}.",
        suffix=".tmp",
        dir=provenance_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
        os.replace(temporary_path, provenance_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return provenance_path


def resample_file(
    input_path: Path,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    require_approval: bool = False,
) -> dict[str, Any]:
    """Write complete UTC M5/M15/H1/H4/D1 bars and provenance."""
    symbol, has_source, rows = _read_m1_file(input_path)
    m1_sha256 = sha256_file(input_path)
    if require_approval:
        require_owner_approved_sanity(
            input_path.parent,
            symbol=symbol,
            timeframe="M1",
            primary_sha256=m1_sha256,
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, dict[str, Any]] = {}

    for timeframe, minutes in INTERVALS.items():
        candles, incomplete = _aggregate(rows, minutes)
        path = output_dir / f"{symbol}_{timeframe}.csv"
        output_hash = _write_candles(path, candles, has_source=has_source)
        outputs[timeframe] = {
            "path": path,
            "bars_written": len(candles),
            "incomplete_buckets_omitted": incomplete,
            "sha256": output_hash,
        }

    provenance_path = _write_provenance(
        output_dir,
        symbol=symbol,
        m1_sha256=m1_sha256,
        m1_rows=len(rows),
        outputs=outputs,
    )
    return {
        "symbol": symbol,
        "m1_sha256": m1_sha256,
        "m1_rows": len(rows),
        "outputs": outputs,
        "provenance_path": provenance_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize UTC M5/M15/H1/H4/D1 CSVs from approved local M1 data."
        )
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        help="one M1 CSV; omit this when using --all",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="rebuild the verified monthly dataset without resolving merged M1 CSVs",
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    if args.all == (args.input is not None):
        parser.error("provide exactly one of an M1 input file or --all.")

    output_dir = args.output_dir or (
        args.data_dir if args.all else DEFAULT_OUTPUT_DIR
    )
    try:
        processed = 0
        if args.all:
            monthly_manifest = args.data_dir / "XAUUSD" / "monthly-manifest.json"
            if monthly_manifest.is_file():
                if __package__:
                    from .monthly_data import regenerate_monthly_outputs
                else:
                    from monthly_data import regenerate_monthly_outputs
                result = regenerate_monthly_outputs(
                    args.data_dir,
                    output_root=output_dir,
                )
                print(
                    f"{result['symbol']}: raw-fidelity verified "
                    f"({result['source_sha256']}); {result['month_count']} months; "
                    f"{result['files_written']} written, "
                    f"{result['files_unchanged']} unchanged"
                )
                for timeframe, count in result["timeframe_counts"].items():
                    print(f"  {timeframe}: {count} monthly files")
                processed += 1
            input_paths: list[Path] = []
        else:
            input_paths = [args.input]

        for input_path in input_paths:
            result = resample_file(
                input_path,
                output_dir=output_dir,
                require_approval=True,
            )
            print(
                f"{result['symbol']}: {result['m1_rows']:,} M1 rows "
                f"({result['m1_sha256']}); provenance {result['provenance_path']}"
            )
            for timeframe, output in result["outputs"].items():
                print(
                    f"  {timeframe}: {output['bars_written']:,} complete bars; "
                    f"{output['incomplete_buckets_omitted']:,} incomplete intervals "
                    f"omitted; {output['path']}"
                )
            processed += 1

        if args.all and processed == 0:
            print(f"No verified monthly dataset found under {args.data_dir}; nothing rebuilt.")
        if not processed:
            raise ValueError(
                f"No approved M1 input or XAUUSD monthly manifest was found in {args.data_dir}."
            )
    except (OSError, ValueError) as exc:
        print(f"Resampling stopped: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
