"""Populate local shadow-feature snapshots with simulated trade outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Sequence

from . import backtest
from .alert_snapshots import DEFAULT_SNAPSHOT_PATH, read_snapshots
from .candle_data import CandleFileData, DEFAULT_DATA_DIR, sha256_file
from .data_sanity import require_owner_approved_sanity, sanity_manifest_path
from .digest import build_digest
from .monthly_data import load_monthly_manifest, load_monthly_timeframe, verify_monthly_gate
from .replay import REPLAY_ADVANCE_CHUNK, ReplaySession
from .resample import INTERVALS, _aggregate, _read_m1_file

DEFAULT_INPUT = DEFAULT_DATA_DIR
SNAPSHOT_YEAR = 2025


def _window_months(from_date: str, to_date: str) -> set[str]:
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    if start > end:
        raise ValueError("The snapshot window start date must not be after its end date.")
    months: set[str] = set()
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.add(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return months


def _require_pending_test_manifest(input_path: Path, source_hash: str) -> None:
    manifest_path = sanity_manifest_path(input_path.parent, "XAUUSD", "M1")
    if not manifest_path.is_file():
        raise ValueError("Test override requires an existing pending XAUUSD M1 manifest.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Test override requires a valid XAUUSD M1 manifest.") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Test override requires a valid XAUUSD M1 manifest.")
    if (
        manifest.get("symbol") != "XAUUSD"
        or manifest.get("timeframe") != "M1"
        or manifest.get("status") != "PENDING_OWNER_SPOTCHECK"
        or manifest.get("review_method") != "OWNER_SPOTCHECK"
        or manifest.get("owner_approved") is not False
        or manifest.get("data_complete") is not True
        or manifest.get("primary_sha256") != source_hash
    ):
        raise ValueError(
            "Test override only accepts a complete, hash-matched "
            "PENDING_OWNER_SPOTCHECK manifest."
        )


def _digest_result(
    rows: list[dict],
    run_id: str,
    from_date: str,
    to_date: str,
) -> tuple[dict, int]:
    digest = build_digest(
        rows,
        run_id=run_id,
        period_label=f"{from_date} through {to_date}",
    )
    expectancy_cells = sum(
        group.get("expectancy_after_costs") is not None
        for group in digest["groups"]
    )
    return digest, expectancy_cells


def populate_snapshots(
    *,
    input_path: Path = DEFAULT_INPUT,
    output_path: Path = DEFAULT_SNAPSHOT_PATH,
    from_date: str = f"{SNAPSHOT_YEAR}-01-01",
    to_date: str = f"{SNAPSHOT_YEAR}-12-31",
    allow_pending_for_test: bool = False,
) -> dict[str, object]:
    expected_months = _window_months(from_date, to_date)
    monthly_mode = (
        input_path.is_dir()
        and (input_path / "XAUUSD" / "M1").is_dir()
    )
    monthly_manifest: dict[str, object] | None = None
    if monthly_mode:
        try:
            monthly_manifest = load_monthly_manifest(input_path, symbol="XAUUSD")
            source_hash = verify_monthly_gate(
                input_path,
                manifest=monthly_manifest,
            )
            m1_data = load_monthly_timeframe(
                input_path,
                "XAUUSD",
                "M1",
                start_month=min(expected_months),
                end_month=max(expected_months),
                manifest=monthly_manifest,
            )
        except (OSError, ValueError) as exc:
            raise ValueError(f"Could not load verified monthly XAUUSD data: {exc}") from exc
        symbol = "XAUUSD"
        m1_records = m1_data.candles
    else:
        symbol, _has_source, m1_records = _read_m1_file(input_path)
        source_hash = sha256_file(input_path)
    if symbol != "XAUUSD":
        raise ValueError(f"Expected XAUUSD M1 candles; got {symbol}.")
    source_values = {
        str(record.get("source", "")).strip().lower()
        for record in m1_records
        if str(record.get("source", "")).strip()
    }
    if source_values != {"dukascopy"}:
        raise ValueError("Snapshot population requires local Dukascopy XAUUSD data.")

    year_records = [
        record
        for record in m1_records
        if from_date <= str(record["time"])[:10] <= to_date
    ]
    months = {str(record["time"])[:7] for record in year_records}
    if not year_records:
        raise ValueError(f"No XAUUSD M1 candles exist in {from_date} through {to_date}.")
    if not expected_months.issubset(months):
        missing_months = sorted(expected_months - months)
        raise ValueError(
            "Snapshot population is missing calendar months in the requested window: "
            + ", ".join(missing_months)
        )

    if allow_pending_for_test:
        if monthly_mode:
            raise ValueError("Pending test override only accepts a standalone test CSV.")
        if output_path.resolve() == DEFAULT_SNAPSHOT_PATH.resolve():
            raise ValueError(
                "Pending test override must write to a separate test-only output file."
            )
        _require_pending_test_manifest(input_path, source_hash)
    elif not monthly_mode:
        require_owner_approved_sanity(
            input_path.parent,
            symbol="XAUUSD",
            timeframe="M1",
            primary_sha256=source_hash,
        )
    loaded: dict[tuple[str, str], CandleFileData] = {}
    for timeframe, minutes in INTERVALS.items():
        if monthly_mode:
            assert monthly_manifest is not None
            timeframe_data = load_monthly_timeframe(
                input_path,
                "XAUUSD",
                timeframe,
                start_month=min(expected_months),
                end_month=max(expected_months),
                manifest=monthly_manifest,
            )
            records = [
                record
                for record in timeframe_data.candles
                if from_date <= str(record["time"])[:10] <= to_date
            ]
            source = timeframe_data.source
            timeframe_path = timeframe_data.path
        else:
            records, _incomplete = _aggregate(year_records, minutes)
            source = "dukascopy"
            timeframe_path = input_path
        loaded[("XAUUSD", timeframe)] = CandleFileData(
            candles=records,
            path=timeframe_path,
            source=source,
            sha256=source_hash,
        )
    if any(not data.candles for data in loaded.values()):
        raise ValueError("No complete XAUUSD M5/M15 candles were produced.")

    spec = backtest.load_spec(backtest.DEFAULT_SPEC)
    spec = {**spec, "instruments": ["XAUUSD"], "timeframes": ["M5", "M15"]}
    fingerprint = hashlib.sha256(
        (
            backtest.settings_fingerprint(spec)
            + ":snapshot-simulation-v1:enabled:1pct-risk:2r"
            + f":window:{from_date}:{to_date}"
            + f":pending-test-override:{int(allow_pending_for_test)}"
        ).encode("utf-8")
    ).hexdigest()
    run_id = backtest._run_id(fingerprint, loaded)
    existing = read_snapshots(output_path)
    existing_run = [row for row in existing if row.get("run_id") == run_id]
    if existing_run:
        observed_months = {
            timeframe: {
                str(row.get("period"))
                for row in existing_run
                if row.get("tf") == timeframe
            }
            for timeframe in ("M5", "M15")
        }
        if any(observed_months[timeframe] != expected_months for timeframe in observed_months):
            raise ValueError(
                f"Snapshot run {run_id} is already present but does not cover every "
                "month in both timeframes; choose a new output file to avoid mixing rows."
            )
        resolved = sum(
            row.get("sim_outcome") in {"WIN", "LOSS"} for row in existing_run
        )
        if resolved == 0:
            raise ValueError(
                f"Snapshot run {run_id} is present but has no resolved simulated trades; "
                "choose a new output file to regenerate it."
            )
        digest, expectancy_cells = _digest_result(
            existing_run,
            run_id,
            from_date,
            to_date,
        )
        if expectancy_cells == 0:
            raise ValueError(
                "The existing snapshot run has no qualifying expectancy cells."
            )
        print_rows = len(existing_run)
        return {
            "run_id": run_id,
            "source_sha256": source_hash,
            "source": str(input_path),
            "output": str(output_path),
            "window": {"from": from_date, "to": to_date},
            "approval_override_for_test": allow_pending_for_test,
            "candle_counts": {
                timeframe: len(loaded[("XAUUSD", timeframe)].candles)
                for timeframe in ("M5", "M15")
            },
            "snapshot_counts": {
                timeframe: sum(row.get("tf") == timeframe for row in existing_run)
                for timeframe in ("M5", "M15")
            },
            "rows": print_rows,
            "resolved_trade_snapshots": sum(
                row.get("sim_outcome") in {"WIN", "LOSS"} for row in existing_run
            ),
            "expectancy_cells": expectancy_cells,
            "digest": digest,
            "scored": True,
            "already_present": True,
        }

    for timeframe in ("M5", "M15"):
        data = loaded[("XAUUSD", timeframe)]
        records = [
            {
                "time": str(row["time"]),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
            }
            for row in data.candles
        ]
        session = ReplaySession(
            session_id=f"snapshot-{uuid.uuid4().hex}",
            run_id=run_id,
            timeframe=timeframe,
            from_value=from_date,
            to_value=to_date,
            records=records,
            source=data.source,
            source_sha256=source_hash,
            spec=spec,
            settings_fingerprint=fingerprint,
            simulation_settings={
                "enabled": True,
                "startingBalance": 10000,
                "riskPercent": 1,
                "rewardMultiple": 2,
            },
            snapshot_path=output_path,
        )
        session.initial_response()
        last_index = len(records) - 1
        while session.computed_through < last_index:
            target = min(
                last_index,
                session.computed_through + REPLAY_ADVANCE_CHUNK,
            )
            session.advance(
                to_index=target,
                view_start=max(0, target - 999),
                view_end=target,
                simulation_filters=None,
            )
        session.flush_snapshots()

    rows_for_run = [row for row in read_snapshots(output_path) if row.get("run_id") == run_id]
    snapshot_counts = {
        timeframe: sum(row.get("tf") == timeframe for row in rows_for_run)
        for timeframe in ("M5", "M15")
    }
    resolved_trade_snapshots = sum(
        row.get("sim_outcome") in {"WIN", "LOSS"} for row in rows_for_run
    )
    digest, expectancy_cells = _digest_result(
        rows_for_run,
        run_id,
        from_date,
        to_date,
    )
    if expectancy_cells == 0:
        raise ValueError(
            "Snapshot population completed, but the digest has no qualifying "
            "expectancy cells with resolved simulated trades."
        )
    return {
        "run_id": run_id,
        "source_sha256": source_hash,
        "source": str(input_path),
        "output": str(output_path),
        "window": {"from": from_date, "to": to_date},
        "approval_override_for_test": allow_pending_for_test,
        "candle_counts": {
            timeframe: len(loaded[("XAUUSD", timeframe)].candles)
            for _symbol, timeframe in loaded
        },
        "snapshot_counts": snapshot_counts,
        "rows": len(rows_for_run),
        "resolved_trade_snapshots": resolved_trade_snapshots,
        "expectancy_cells": expectancy_cells,
        "digest": digest,
        "scored": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Write simulated XAUUSD M5/M15 snapshots from a bounded local M1 window."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument("--from-date", default=f"{SNAPSHOT_YEAR}-01-01")
    parser.add_argument("--to-date", default=f"{SNAPSHOT_YEAR}-12-31")
    parser.add_argument(
        "--allow-pending-for-test",
        action="store_true",
        help="Allow only a complete, hash-matched pending manifest and a separate output file.",
    )
    args = parser.parse_args(argv)
    try:
        result = populate_snapshots(
            input_path=args.input,
            output_path=args.output,
            from_date=args.from_date,
            to_date=args.to_date,
            allow_pending_for_test=args.allow_pending_for_test,
        )
    except (OSError, ValueError) as exc:
        print(f"Snapshot population stopped: {exc}", file=sys.stderr)
        return 2

    already_present = bool(result.get("already_present"))
    print(
        f"XAUUSD snapshots for {result['window']['from']} through "
        f"{result['window']['to']} already present; no duplicate rows written."
        if already_present
        else (
            f"XAUUSD snapshots for {result['window']['from']} through "
            f"{result['window']['to']} completed with simulation outcomes."
        )
    )
    print(f"Run ID: {result['run_id']}")
    print(f"Source SHA256: {result['source_sha256']}")
    print(f"Output: {result['output']}")
    print(f"Pending-data test override: {result['approval_override_for_test']}")
    print(f"M5 candles: {result['candle_counts']['M5']:,}")
    print(f"M15 candles: {result['candle_counts']['M15']:,}")
    print(f"M5 snapshots: {result['snapshot_counts']['M5']:,}")
    print(f"M15 snapshots: {result['snapshot_counts']['M15']:,}")
    print(f"Valid JSONL rows: {result['rows']:,}")
    print(f"Resolved trade snapshots: {result['resolved_trade_snapshots']:,}")
    print(f"Qualifying expectancy cells: {result['expectancy_cells']:,}")
    print("Simulation outcomes and expectancy digest recorded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
