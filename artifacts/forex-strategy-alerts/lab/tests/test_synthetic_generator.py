import tempfile
import unittest
from datetime import date
from pathlib import Path

from lab.generate_synthetic_data import DATA_DIR, generate_file, month_window


class SyntheticGeneratorTests(unittest.TestCase):
    def test_default_output_is_isolated_in_synthetic_directory(self):
        self.assertEqual(DATA_DIR.name, "synthetic")
        self.assertEqual(DATA_DIR.parent.name, "data")

    def test_month_window_is_calendar_aligned(self):
        start, end = month_window(date(2026, 10, 27))
        self.assertEqual(start, date(2025, 11, 1))
        self.assertEqual(end, date(2026, 10, 27))

    def test_seed_produces_identical_output(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_path, first_rows = generate_file(
                "GBPUSD",
                "M5",
                date(2026, 1, 5),
                date(2026, 1, 6),
                140,
                Path(first),
            )
            second_path, second_rows = generate_file(
                "GBPUSD",
                "M5",
                date(2026, 1, 5),
                date(2026, 1, 6),
                140,
                Path(second),
            )
            self.assertEqual(first_rows, 2 * 24 * 60 // 5)
            self.assertEqual(first_rows, second_rows)
            self.assertEqual(first_path.name, "GBPUSD_M5.csv")
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

    def test_generation_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            generate_file(
                "GBPUSD",
                "M5",
                date(2026, 1, 5),
                date(2026, 1, 5),
                140,
                output_dir,
            )
            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                generate_file(
                    "GBPUSD",
                    "M5",
                    date(2026, 1, 5),
                    date(2026, 1, 5),
                    141,
                    output_dir,
                )


if __name__ == "__main__":
    unittest.main()
