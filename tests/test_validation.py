import unittest
from pathlib import Path

from weekly_assistant.adapters.csv_adapter import load_weekly_rows
from weekly_assistant.services.validation import validate_rows


FIXTURE = Path(__file__).parent / "fixtures" / "weekly_mvp_sample.csv"


class ValidationTest(unittest.TestCase):
    def test_accepts_known_named_ball_sides(self):
        rows = load_weekly_rows(FIXTURE)
        issues = validate_rows(rows)

        self.assertFalse(any(issue.field == "На чьей стороне мяч" for issue in issues))
        self.assertFalse(any(issue.severity == "error" for issue in issues))


if __name__ == "__main__":
    unittest.main()
