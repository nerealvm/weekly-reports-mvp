import unittest
from pathlib import Path

from weekly_assistant.adapters.csv_adapter import load_weekly_rows
from weekly_assistant.domain.enums import Lifecycle


FIXTURE = Path(__file__).parent / "fixtures" / "weekly_mvp_sample.csv"


class CsvAdapterTest(unittest.TestCase):
    def test_loads_weekly_rows(self):
        rows = load_weekly_rows(FIXTURE)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].topic_title, "Геш Групп")
        self.assertEqual(rows[2].lifecycle, Lifecycle.PAUSED)


if __name__ == "__main__":
    unittest.main()
