import unittest
from pathlib import Path

from weekly_assistant.adapters.csv_adapter import load_weekly_rows
from weekly_assistant.domain.enums import Lifecycle, MovementType, ReviewStatus
from weekly_assistant.services.weekly_engine import create_next_week_rows


FIXTURE = Path(__file__).parent / "fixtures" / "weekly_mvp_sample.csv"


class CreateWeekTest(unittest.TestCase):
    def test_creates_active_only_week_by_default(self):
        rows = load_weekly_rows(FIXTURE)

        next_rows = create_next_week_rows(rows)

        self.assertEqual(len(next_rows), 2)
        self.assertTrue(all(row.lifecycle == Lifecycle.ACTIVE for row in next_rows))
        self.assertTrue(all(row.current_week_facts == "" for row in next_rows))
        self.assertTrue(all(row.ai_draft_result == "" for row in next_rows))
        self.assertTrue(all(row.final_result == "" for row in next_rows))
        self.assertTrue(all(row.review_status == ReviewStatus.DRAFT for row in next_rows))

    def test_next_week_starts_with_unclear_movement_without_current_facts(self):
        rows = load_weekly_rows(FIXTURE)

        next_rows = create_next_week_rows(rows)

        self.assertEqual(next_rows[0].movement_type, MovementType.UNCLEAR)


if __name__ == "__main__":
    unittest.main()
