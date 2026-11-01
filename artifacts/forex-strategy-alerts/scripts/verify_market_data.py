#!/usr/bin/env python3
"""Verify tracked market-data sources against their plain-file SHA-256 inventory."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "lab" / "data"
MANIFEST = DATA_ROOT / "market-data.sha256"
POINTER_VERSION = b"version https://git-lfs.github.com/spec/v1"
DERIVED_SUFFIXES = ("_M5.csv", "_M15.csv", "_H1.csv", "_H4.csv", "_D1.csv")


def git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stderr=subprocess.PIPE,
    )


def manifest_entries() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, line in enumerate(MANIFEST.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        digest, path = line.split(maxsplit=1)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid SHA-256 on manifest line {line_number}")
        if not path.startswith("lab/data/") or not path.endswith(".csv"):
            raise ValueError(f"Unexpected dataset path on manifest line {line_number}")
        if path in entries:
            raise ValueError(f"Duplicate manifest path: {path}")
        entries[path] = digest
    return entries


def indexed_paths() -> set[str]:
    return set(git_output("ls-files", "--cached", "--", "lab/data").splitlines())


def revision_paths(revision: str) -> set[str]:
    return set(
        git_output("ls-tree", "-r", "--name-only", revision, "--", "lab/data").splitlines()
    )


def is_derived_path(path: str) -> bool:
    candidate = Path(path)
    return candidate.parent == Path("lab/data") and candidate.name.endswith(
        DERIVED_SUFFIXES
    )


def object_for(path: str, index: bool, revision: str) -> bytes:
    object_name = f":{path}" if index else f"{revision}:{path}"
    return subprocess.check_output(
        ["git", "show", object_name],
        cwd=ROOT,
        stderr=subprocess.PIPE,
    )


def verify(index: bool, revision: str, *, worktree: bool = False) -> list[str]:
    errors: list[str] = []
    expected = manifest_entries()
    if not expected:
        return ["The market-data manifest is empty."]
    derived_in_manifest = sorted(path for path in expected if is_derived_path(path))
    if derived_in_manifest:
        errors.append(
            "Regenerable timeframe CSVs must not be in the source inventory: "
            + ", ".join(derived_in_manifest)
        )

    disk_paths = {
        path.relative_to(ROOT).as_posix()
        for path in DATA_ROOT.rglob("*.csv")
        if not is_derived_path(path.relative_to(ROOT).as_posix())
    }
    if disk_paths != set(expected):
        missing = sorted(set(expected) - disk_paths)
        extra = sorted(disk_paths - set(expected))
        if missing:
            errors.append(f"Missing worktree CSVs: {', '.join(missing)}")
        if extra:
            errors.append(f"Unmanifested worktree CSVs: {', '.join(extra)}")

    tracked_paths = (
        set()
        if worktree
        else indexed_paths()
        if index
        else revision_paths(revision)
    )
    tracked_csvs = {
        path for path in tracked_paths
        if path.startswith("lab/data/")
        and path.endswith(".csv")
        and not is_derived_path(path)
    }
    if not worktree and tracked_csvs != set(expected):
        missing = sorted(set(expected) - tracked_csvs)
        extra = sorted(tracked_csvs - set(expected))
        if missing:
            errors.append(f"CSV files absent from {'index' if index else revision}: {', '.join(missing)}")
        if extra:
            errors.append(f"Unmanifested CSVs in {'index' if index else revision}: {', '.join(extra)}")

    manifest_path = MANIFEST.relative_to(ROOT).as_posix()
    if not worktree and manifest_path not in tracked_paths:
        errors.append(f"{manifest_path} is not included in the {'index' if index else revision}.")

    for path, expected_oid in expected.items():
        try:
            disk_path = ROOT / path
            if not disk_path.is_file():
                continue
            disk_hash = hashlib.sha256(disk_path.read_bytes()).hexdigest()
            if disk_hash != expected_oid:
                errors.append(f"{path} worktree hash differs from {MANIFEST.relative_to(ROOT)}.")

            if worktree:
                continue

            attribute = git_output("check-attr", "filter", "--", path).strip()
            if attribute.endswith(": filter: lfs"):
                errors.append(f"{path} unexpectedly uses Git LFS.")
                continue
            stored_bytes = object_for(path, index, revision)
            if stored_bytes.startswith(POINTER_VERSION):
                errors.append(f"{path} is a Git LFS pointer, not a source CSV.")
                continue
            actual_hash = hashlib.sha256(stored_bytes).hexdigest()
            if actual_hash != expected_oid:
                errors.append(f"{path} stored hash differs from {MANIFEST.relative_to(ROOT)}.")
        except (OSError, subprocess.CalledProcessError, UnicodeDecodeError, ValueError) as error:
            errors.append(f"Could not verify {path}: {error}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--index",
        action="store_true",
        help="verify staged paths and file hashes before committing",
    )
    source.add_argument(
        "--revision",
        default="HEAD",
        help="verify a committed tree (default: HEAD)",
    )
    source.add_argument(
        "--worktree",
        action="store_true",
        help="verify the source CSV inventory and hashes on disk",
    )
    args = parser.parse_args()

    errors = verify(args.index, args.revision, worktree=args.worktree)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    count = len(manifest_entries())
    target = "worktree" if args.worktree else "staged index" if args.index else args.revision
    print(f"Verified {count} tracked-source market-data CSVs in {target}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
