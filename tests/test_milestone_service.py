import unittest

from weekly_assistant.services.milestone_service import extract_first_milestone_date


class MilestoneServiceTest(unittest.TestCase):
    def test_extracts_first_date(self):
        self.assertEqual(extract_first_milestone_date("08.05 Финализированный документ"), "08.05")


if __name__ == "__main__":
    unittest.main()
