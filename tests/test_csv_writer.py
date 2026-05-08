import tempfile
import unittest
from pathlib import Path

from weekly_assistant.adapters.csv_adapter import load_weekly_rows, write_weekly_rows


FIXTURE = Path(__file__).parent / "fixtures" / "weekly_mvp_sample.csv"


class CsvWriterTest(unittest.TestCase):
    def test_round_trips_rows(self):
        rows = load_weekly_rows(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "weekly.csv"
            write_weekly_rows(out, rows)

            round_tripped = load_weekly_rows(out)

        self.assertEqual(round_tripped, rows)


if __name__ == "__main__":
    unittest.main()
