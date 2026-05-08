import unittest

from weekly_assistant.services.incoming_service import normalize_incoming_item


class IncomingServiceTest(unittest.TestCase):
    def test_detects_weekly_destination(self):
        item = normalize_incoming_item("manual", "Отдать Володе новую задачу по weekly")

        self.assertEqual(item.proposed_destination, "weekly")


if __name__ == "__main__":
    unittest.main()
