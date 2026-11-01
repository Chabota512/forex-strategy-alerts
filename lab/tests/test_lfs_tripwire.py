from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


MONTHLY_TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")
MONTHLY_MONTHS = tuple(
    f"{year:04d}-{month:02d}"
    for year in (2023, 2024, 2025)
    for month in range(1, 13)
)
RAW_ARCHIVES = (
    "lab/data/historicalMarketData/XAUUSD/XAUUSD_M1_BID_2023_UTC.csv",
    "lab/data/historicalMarketData/XAUUSD/XAUUSD_M1_BID_2024_UTC.csv",
    "lab/data/historicalMarketData/XAUUSD/XAUUSD_M1_BID_2025_UTC.csv",
)


class GitLfsTripwireTests(unittest.TestCase):
    def test_tracked_lab_data_has_no_lfs_attributes_or_pointer_files(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", "lab/data"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        tracked_paths = [
            path for path in result.stdout.decode().split("\0") if path
        ]
        if not tracked_paths:
            self.skipTest(
                "Tracked-file checks require a Git checkout; this snapshot has no tracked files."
            )
        tracked_monthly = [
            path
            for path in tracked_paths
            if Path(path).parent.parent == Path("lab/data/XAUUSD")
            and Path(path).parent.name in MONTHLY_TIMEFRAMES
            and Path(path).suffix == ".csv"
        ]
        expected_monthly = sorted(
            f"lab/data/XAUUSD/{timeframe}/{month}.csv"
            for timeframe in MONTHLY_TIMEFRAMES
            for month in MONTHLY_MONTHS
        )
        self.assertEqual(
            sorted(tracked_monthly),
            expected_monthly,
            "All 216 verified XAUUSD monthly CSVs must be tracked",
        )
        manifest_path = "lab/data/XAUUSD/monthly-manifest.json"
        self.assertIn(manifest_path, tracked_paths)
        for raw_path in RAW_ARCHIVES:
            self.assertIn(raw_path, tracked_paths)
        attribute_result = subprocess.run(
            [
                "git",
                "check-attr",
                "filter",
                "--",
                manifest_path,
                *tracked_monthly,
                *RAW_ARCHIVES,
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue(
            all(
                "filter: unspecified" in line
                for line in attribute_result.stdout.splitlines()
            ),
            "Tracked monthly data and raw files must not use Git LFS",
        )
        lfs_signature = b"version https://git-lfs.github.com/spec/v1"
        pointer_paths = []

        for relative_path in tracked_paths:
            path = repo_root / relative_path
            self.assertTrue(path.is_file(), f"Tracked data file is missing: {relative_path}")
            with path.open("rb") as tracked_file:
                prefix = tracked_file.read(len(lfs_signature))
            if prefix == lfs_signature:
                pointer_paths.append(relative_path)

        self.assertEqual(pointer_paths, [], "Git LFS pointer files remain tracked")

    def test_monthly_gold_dataset_is_not_gitignored(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        monthly_files = [
            f"lab/data/XAUUSD/{timeframe}/{month}.csv"
            for timeframe in MONTHLY_TIMEFRAMES
            for month in MONTHLY_MONTHS
        ]
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin"],
            cwd=repo_root,
            input=("\n".join(monthly_files) + "\n").encode(),
            check=False,
            capture_output=True,
        )
        self.assertEqual(
            result.returncode,
            1,
            "Verified monthly CSVs must not match any ignore rule",
        )
        self.assertEqual(result.stdout, b"", "An XAUUSD monthly CSV is ignored")


if __name__ == "__main__":
    unittest.main()
