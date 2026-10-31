"""Compare local candle CSVs to an independent local reference; no network calls."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

if __package__:
    from .candle_data import (
        DEFAULT_DATA_DIR,
        CandleFileData,
        load_candle_file,
        parse_timestamp,
        sha256_file,
    )
else:
    from candle_data import (
        DEFAULT_DATA_DIR,
        CandleFileData,
        load_candle_file,
        parse_timestamp,
        sha256_file,
    )

SANITY_DIRNAME = "sanity_checks"
OWNER_ACCEPTED_REVIEW_METHOD = "OWNER_ACCEPTED_WITHOUT_EXTERNAL_CHECK"
OWNER_ACCEPTED_APPROVAL_BASIS = "owner-accepted without external check (§27)"
DERIVED_TIMEFRAMES = frozenset({"M5", "M15", "H1", "H4", "D1"})


def sanity_manifest_path(data_dir: Path, symbol: str, timeframe: str) -> Path:
    return data_dir / SANITY_DIRNAME / f"{symbol.upper()}_{timeframe.upper()}.json"


def _read_reference_closes(path: Path) -> dict[str, Decimal]:
    closes: dict[str, Decimal] = {}
    previous_timestamp: datetime | None = None

    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        headers = {
            (name or "").strip().lower(): name
            for name in (reader.fieldnames or [])
        }
        time_column = next(
            (headers[name] for name in ("time", "datetime", "date", "timestamp") if name in headers),
            None,
        )
        close_column = headers.get("close")
        if time_column is None or close_column is None:
            raise ValueError(
                f"{path}: reference CSV needs a time/datetime/date and close column"
            )

        for line_number, row in enumerate(reader, start=2):
            raw_time = row.get(time_column) or ""
            try:
                raw_timestamp = datetime.fromisoformat(
                    raw_time.strip().replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid reference timestamp {raw_time!r}"
                ) from exc
            if raw_timestamp.tzinfo is None:
                raise ValueError(
                    f"{path}:{line_number}: reference timestamps must include a UTC offset"
                )
            timestamp = parse_timestamp(raw_time, path, line_number)
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError(
                    f"{path}:{line_number}: reference timestamps must be strictly increasing"
                )
            previous_timestamp = timestamp
            try:
                close = Decimal(row.get(close_column) or "")
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid reference close"
                ) from exc
            if not close.is_finite() or close <= 0:
                raise ValueError(f"{path}:{line_number}: reference close must be positive")
            key = timestamp.isoformat()
            if key in closes:
                raise ValueError(f"{path}:{line_number}: duplicate reference timestamp")
            closes[key] = close

    if not closes:
        raise ValueError(f"{path}: no reference candles found")
    return closes


def _sample_indices(total: int, limit: int) -> list[int]:
    if total <= 0 or limit <= 0:
        return []
    if total <= limit:
        return list(range(total))
    if limit == 1:
        return [0]
    return sorted({round(index * (total - 1) / (limit - 1)) for index in range(limit)})


def compare_candle_sources(
    primary_path: Path,
    reference_path: Path,
    *,
    symbol: str,
    timeframe: str,
    report_path: Path,
    max_samples: int = 20,
) -> dict[str, Any]:
    """Write a pending-review comparison for exact matching UTC timestamps.

    This intentionally does not resample, fetch data, define an acceptable price
    tolerance, or approve the input automatically.
    """
    if max_samples < 1:
        raise ValueError("max_samples must be at least 1")

    primary: CandleFileData = load_candle_file(primary_path)
    reference_closes = _read_reference_closes(reference_path)
    primary_by_time = {
        datetime.fromisoformat(candle["time"].replace("Z", "+00:00")).isoformat(): candle
        for candle in primary.candles
    }
    common_times = sorted(set(primary_by_time) & set(reference_closes))
    primary_only = sorted(set(primary_by_time) - set(reference_closes))
    reference_only = sorted(set(reference_closes) - set(primary_by_time))

    comparisons: list[dict[str, str]] = []
    nonzero_differences = 0
    max_abs_difference = Decimal("0")
    max_relative_bps = Decimal("0")
    for time_index in _sample_indices(len(common_times), max_samples):
        timestamp = common_times[time_index]
        primary_close = primary_by_time[timestamp]["close"]
        reference_close = reference_closes[timestamp]
        delta = reference_close - primary_close
        abs_delta = abs(delta)
        relative_bps = abs_delta / primary_close * Decimal("10000")
        if abs_delta:
            nonzero_differences += 1
        max_abs_difference = max(max_abs_difference, abs_delta)
        max_relative_bps = max(max_relative_bps, relative_bps)
        comparisons.append(
            {
                "time_utc": timestamp.replace("+00:00", "Z"),
                "primary_close": format(primary_close, "f"),
                "reference_close": format(reference_close, "f"),
                "reference_minus_primary": format(delta, "f"),
                "absolute_difference": format(abs_delta, "f"),
                "absolute_difference_bps_of_primary": format(relative_bps, "f"),
            }
        )

    if not common_times:
        status = "NO_MATCHING_TIMESTAMPS"
    else:
        status = "PENDING_OWNER_REVIEW"

    report: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "owner_approved": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "symbol": symbol.upper(),
        "timeframe": timeframe.upper(),
        "comparison_method": "exact UTC timestamp close-to-close; no resampling",
        "primary_file": str(primary_path),
        "primary_source": primary.source,
        "primary_sha256": primary.sha256,
        "reference_file": str(reference_path),
        "reference_sha256": sha256_file(reference_path),
        "comparison": {
            "primary_rows": len(primary_by_time),
            "reference_rows": len(reference_closes),
            "matched_timestamps": len(common_times),
            "sampled_timestamps": len(comparisons),
            "primary_only_timestamps": len(primary_only),
            "reference_only_timestamps": len(reference_only),
            "sampled_nonzero_close_differences": nonzero_differences,
            "maximum_sampled_absolute_difference": format(max_abs_difference, "f"),
            "maximum_sampled_absolute_difference_bps": format(max_relative_bps, "f"),
            "price_tolerance": None,
            "samples": comparisons,
        },
        "review_note": (
            "No pass/fail tolerance is assumed. Review the matched timestamps, "
            "price differences, and source conventions before approving real-data use."
        ),
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def require_owner_approved_sanity(
    data_dir: Path,
    *,
    symbol: str,
    timeframe: str,
    primary_sha256: str,
) -> None:
    path = sanity_manifest_path(data_dir, symbol, timeframe)
    if not path.is_file():
        raise ValueError(
            f"Real-data sanity check required for {symbol} {timeframe}. "
            f"Compare it with an independent local reference and review {path}."
        )
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read sanity-check report {path}: {exc}") from exc
    if report.get("symbol") != symbol.upper() or report.get("timeframe") != timeframe.upper():
        raise ValueError(f"{path}: sanity report symbol/timeframe does not match input")
    if report.get("primary_sha256") != primary_sha256:
        raise ValueError(
            f"{path}: input data changed since its sanity check; compare it again"
        )

    if report.get("review_method") == OWNER_ACCEPTED_REVIEW_METHOD:
        if (
            report.get("status") != "OWNER_APPROVED"
            or report.get("owner_approved") is not True
        ):
            raise ValueError(
                f"Real-data sanity check for {symbol} {timeframe} is not owner-approved; "
                "owner acceptance is pending."
            )
        if report.get("approval_basis") != OWNER_ACCEPTED_APPROVAL_BASIS:
            raise ValueError(
                f"{path}: approval_basis must identify owner acceptance under plan §27."
            )
        if report.get("data_complete") is not True:
            raise ValueError(
                f"{path}: the complete training dataset is required before approval."
            )
        return

    if report.get("review_method") == "OWNER_SPOTCHECK":
        if report.get("status") != "OWNER_APPROVED":
            raise ValueError(
                f"Real-data sanity check for {symbol} {timeframe} is not owner-approved; "
                "owner spot-check is pending."
            )
        if report.get("data_complete") is not True:
            raise ValueError(
                f"{path}: the complete 2023–2025 dataset is required before approval."
            )
        return

    if (
        report.get("status") != "OWNER_APPROVED"
        or report.get("owner_approved") is not True
    ):
        raise ValueError(
            f"Real-data sanity check for {symbol} {timeframe} is not owner-approved. "
            "Backtest scoring is blocked until the comparison is reviewed."
        )
    reference_value = report.get("reference_file")
    reference_hash = report.get("reference_sha256")
    if not isinstance(reference_value, str) or not isinstance(reference_hash, str):
        raise ValueError(f"{path}: sanity report is missing reference provenance")
    reference_path = Path(reference_value)
    if not reference_path.is_file() or sha256_file(reference_path) != reference_hash:
        raise ValueError(
            f"{path}: reference data is missing or changed since its sanity check"
        )


def require_owner_approved_derived_timeframe(
    data_dir: Path,
    *,
    symbol: str,
    timeframe: str,
    m1_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate derived data against provenance and its owner-approved M1 source."""
    symbol = symbol.strip().upper()
    timeframe = timeframe.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]+", symbol):
        raise ValueError("symbol must contain only letters and numbers")
    if timeframe not in DERIVED_TIMEFRAMES:
        raise ValueError(
            f"Derived timeframe must be one of {', '.join(sorted(DERIVED_TIMEFRAMES))}."
        )

    if symbol == "XAUUSD" and (
        data_dir / "XAUUSD" / "monthly-manifest.json"
    ).is_file():
        raise ValueError(
            "XAUUSD derived candles use the verified monthly dataset, not a merged M1 file."
        )

    m1_path = data_dir / f"{symbol}_M1.csv"
    if not m1_path.is_file():
        raise ValueError(f"Approved {symbol} M1 data is missing.")
    actual_m1_sha256 = sha256_file(m1_path)
    if m1_sha256 is not None and actual_m1_sha256 != m1_sha256:
        raise ValueError(f"{symbol} M1 data changed while validating {timeframe}.")
    require_owner_approved_sanity(
        data_dir,
        symbol=symbol,
        timeframe="M1",
        primary_sha256=actual_m1_sha256,
    )

    provenance_path = data_dir / "provenance" / f"{symbol}.json"
    if not provenance_path.is_file():
        raise ValueError(
            f"Derived-data provenance is required for {symbol} {timeframe}."
        )
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Could not read derived-data provenance {provenance_path}: {exc}"
        ) from exc
    if (
        not isinstance(provenance, dict)
        or provenance.get("schema_version") != 1
        or provenance.get("symbol") != symbol
        or provenance.get("m1_sha256") != actual_m1_sha256
        or not isinstance(provenance.get("generated_at_utc"), str)
    ):
        raise ValueError(
            f"{provenance_path}: provenance does not match the approved {symbol} M1 source."
        )

    timeframe_records = provenance.get("timeframes")
    record = timeframe_records.get(timeframe) if isinstance(timeframe_records, dict) else None
    derived_path = data_dir / f"{symbol}_{timeframe}.csv"
    if not derived_path.is_file():
        raise ValueError(f"Derived candle data is missing for {symbol} {timeframe}.")
    if not isinstance(record, dict):
        raise ValueError(
            f"{provenance_path}: {timeframe} is not recorded in derived-data provenance."
        )
    expected_hash = record.get("sha256")
    row_count = record.get("row_count")
    if (
        not isinstance(expected_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        or isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count < 1
    ):
        raise ValueError(
            f"{provenance_path}: {timeframe} provenance is incomplete or invalid."
        )
    actual_hash = sha256_file(derived_path)
    if actual_hash != expected_hash:
        raise ValueError(
            f"{derived_path}: data changed since its provenance was generated."
        )
    with derived_path.open("r", encoding="utf-8-sig", newline="") as stream:
        actual_rows = max(0, sum(1 for _ in stream) - 1)
    if actual_rows != row_count:
        raise ValueError(
            f"{derived_path}: row count differs from its provenance record."
        )
    return {
        "path": derived_path,
        "sha256": actual_hash,
        "m1_sha256": actual_m1_sha256,
        "row_count": row_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare local candle CSV closes to an independent local CSV."
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-samples", type=int, default=20)
    args = parser.parse_args(argv)

    report_path = args.report or sanity_manifest_path(
        DEFAULT_DATA_DIR, args.symbol, args.timeframe
    )
    try:
        report = compare_candle_sources(
            args.primary,
            args.reference,
            symbol=args.symbol,
            timeframe=args.timeframe,
            report_path=report_path,
            max_samples=args.max_samples,
        )
    except (OSError, ValueError) as exc:
        print(f"Sanity check stopped: {exc}", file=sys.stderr)
        return 2

    comparison = report["comparison"]
    print(f"Sanity-check report: {report_path}")
    print(f"Status: {report['status']} — owner review is required.")
    print(
        f"Matched timestamps: {comparison['matched_timestamps']}; "
        f"sampled: {comparison['sampled_timestamps']}; "
        f"sampled nonzero close differences: "
        f"{comparison['sampled_nonzero_close_differences']}"
    )
    print(
        "No price tolerance is assumed. Real-data backtest scoring remains blocked "
        "until the owner reviews and approves this report."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
