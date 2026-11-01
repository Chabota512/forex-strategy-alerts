import csv
import tempfile
import unittest
from pathlib import Path

from lab.resample import resample_file


def write_m1(path: Path, minute_indexes: list[int], *, offset: str = "+02:00") -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["time", "open", "high", "low", "close", "source"],
        )
        writer.writeheader()
        for index in minute_indexes:
            hour, minute = divmod(index, 60)
            writer.writerow(
                {
                    "time": f"2026-01-01T{2 + hour:02d}:{minute:02d}:00{offset}",
                    "open": str(100 + index),
                    "high": str(102 + index),
                    "low": str(99 + index),
                    "close": str(101 + index),
                    "source": "dukascopy",
                }
            )


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


class ResampleTests(unittest.TestCase):
    def test_aggregates_complete_intervals_and_normalizes_timestamps_to_utc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "GBPUSD_M1.csv"
            output_dir = root / "resampled"
            write_m1(source, list(range(15)))

            result = resample_file(source, output_dir=output_dir)

            m5 = read_rows(result["outputs"]["M5"]["path"])
            m15 = read_rows(result["outputs"]["M15"]["path"])

        self.assertEqual(result["symbol"], "GBPUSD")
        self.assertEqual(result["outputs"]["M5"]["bars_written"], 3)
        self.assertEqual(result["outputs"]["M15"]["bars_written"], 1)
        self.assertEqual(m5[0], {
            "time": "2026-01-01T00:00:00Z",
            "open": "100",
            "high": "106",
            "low": "99",
            "close": "105",
            "source": "dukascopy",
        })
        self.assertEqual(m15[0], {
            "time": "2026-01-01T00:00:00Z",
            "open": "100",
            "high": "116",
            "low": "99",
            "close": "115",
            "source": "dukascopy",
        })

    def test_incomplete_buckets_are_omitted_and_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "USDJPY_M1.csv"
            output_dir = root / "resampled"
            write_m1(source, [0, 1, 2, 4, 5, 6])

            result = resample_file(source, output_dir=output_dir)

            self.assertEqual(read_rows(result["outputs"]["M5"]["path"]), [])
            self.assertEqual(read_rows(result["outputs"]["M15"]["path"]), [])
            self.assertEqual(
                result["outputs"]["M5"]["incomplete_buckets_omitted"], 2
            )
            self.assertEqual(
                result["outputs"]["M15"]["incomplete_buckets_omitted"], 1
            )

    def test_rejects_non_minute_aligned_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "XAUUSD_M1.csv"
            source.write_text(
                "time,open,high,low,close\n"
                "2026-01-01T00:00:00Z,1,2,0.5,1.5\n"
                "2026-01-01T00:01:01Z,2,3,1,2.5\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "whole-minute boundaries"):
                resample_file(source, output_dir=root / "resampled")


if __name__ == "__main__":
    unittest.main()
