import unittest

from weekly_assistant.domain.enums import MovementType
from weekly_assistant.services.movement_service import determine_movement


class MovementServiceTest(unittest.TestCase):
    def test_detects_no_movement(self):
        self.assertEqual(determine_movement("не двигал"), MovementType.NO_MOVEMENT)

    def test_detects_real_result(self):
        self.assertEqual(determine_movement("Отдал финальный документ"), MovementType.REAL_RESULT)

    def test_unclear_empty(self):
        self.assertEqual(determine_movement(""), MovementType.UNCLEAR)


if __name__ == "__main__":
    unittest.main()
