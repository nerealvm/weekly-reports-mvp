import unittest
from pathlib import Path

from weekly_assistant.adapters.csv_adapter import load_weekly_rows
from weekly_assistant.domain.enums import MovementType, YesNo
from weekly_assistant.services.weekly_engine import refresh_computed_fields


FIXTURE = Path(__file__).parent / "fixtures" / "weekly_mvp_sample.csv"


class WeeklyEngineTest(unittest.TestCase):
    def test_refresh_computed_fields(self):
        row = load_weekly_rows(FIXTURE)[0]

        refreshed = refresh_computed_fields(row)

        self.assertEqual(refreshed.movement_type, MovementType.UNCLEAR)
        self.assertEqual(refreshed.needs_sync, YesNo.YES)


if __name__ == "__main__":
    unittest.main()
