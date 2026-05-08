import unittest
from pathlib import Path

from weekly_assistant.adapters.csv_adapter import load_weekly_rows
from weekly_assistant.services.launch_readiness import check_launch_readiness


FIXTURE = Path(__file__).parent / "fixtures" / "weekly_mvp_sample.csv"


class LaunchReadinessTest(unittest.TestCase):
    def test_sample_is_ready_without_blocking_errors(self):
        rows = load_weekly_rows(FIXTURE)

        readiness = check_launch_readiness(rows)

        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.metrics["active"], 2)


if __name__ == "__main__":
    unittest.main()
