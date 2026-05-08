import tempfile
import unittest
from pathlib import Path

from weekly_assistant.services.full_test import run_full_test


FIXTURE = Path(__file__).parent / "fixtures" / "weekly_mvp_sample.csv"


class FullTestServiceTest(unittest.TestCase):
    def test_runs_full_test_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_full_test(FIXTURE, directory)

            self.assertTrue(result.validation_txt.exists())
            self.assertTrue(result.refreshed_csv.exists())
            self.assertTrue(result.next_week_csv.exists())
            self.assertTrue(result.drafted_csv.exists())
            self.assertTrue(result.questions_txt.exists())
            self.assertTrue(result.sync_txt.exists())
            self.assertTrue(result.summary_txt.exists())


if __name__ == "__main__":
    unittest.main()
