import unittest
from pathlib import Path

from weekly_assistant.adapters.csv_adapter import load_weekly_rows
from weekly_assistant.services.exports import build_open_questions_export, build_summary, build_sync_export


FIXTURE = Path(__file__).parent / "fixtures" / "weekly_mvp_sample.csv"


class ExportsTest(unittest.TestCase):
    def test_questions_export_uses_active_rows_only(self):
        rows = load_weekly_rows(FIXTURE)
        export = build_open_questions_export(rows)

        self.assertIn("Геш Групп", export)
        self.assertNotIn("Мониторинг поддержки", export)

    def test_sync_export(self):
        rows = load_weekly_rows(FIXTURE)
        export = build_sync_export(rows)

        self.assertIn("Геш Групп", export)
        self.assertNotIn("ТЭЦ", export)

    def test_summary(self):
        rows = load_weekly_rows(FIXTURE)
        summary = build_summary(rows)

        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.active, 2)
        self.assertEqual(summary.paused, 1)
        self.assertEqual(summary.needs_sync, 1)


if __name__ == "__main__":
    unittest.main()
