import unittest

from weekly_assistant.adapters.csv_adapter import load_weekly_rows
from weekly_assistant.services.weekly_view_model import _format_ball_side, build_weekly_view_model, view_model_to_dict
from weekly_assistant.viewer_server import _week_label_from_sheet_name


class WeeklyViewModelTest(unittest.TestCase):
    def test_builds_metrics_from_active_rows(self):
        rows = load_weekly_rows("tests/fixtures/weekly_mvp_sample.csv")

        model = build_weekly_view_model(rows, week_label="08.05", source_sheet="Weekly MVP")

        self.assertEqual(model.total_active, 2)
        self.assertEqual(model.open_questions, 1)
        self.assertEqual(model.needs_sync, 1)
        self.assertEqual(model.no_movement, 1)

    def test_sync_rows_sort_first(self):
        rows = load_weekly_rows("tests/fixtures/weekly_mvp_sample.csv")

        model = build_weekly_view_model(rows, week_label="08.05", source_sheet="Weekly MVP")

        self.assertEqual(model.items[0].topic_id, "T-001")
        self.assertEqual(model.items[0].priority, 0)

    def test_view_model_serializes_to_dict(self):
        rows = load_weekly_rows("tests/fixtures/weekly_mvp_sample.csv")

        payload = view_model_to_dict(build_weekly_view_model(rows, week_label="08.05", source_sheet="Weekly MVP"))

        self.assertEqual(payload["week_label"], "08.05")
        self.assertEqual(payload["metrics"]["total_active"], 2)
        self.assertEqual(payload["items"][0]["topic_id"], "T-001")

    def test_week_label_from_sheet_name(self):
        self.assertEqual(_week_label_from_sheet_name("Weekly MVP 2026-05-08"), "08.05.2026")

    def test_ball_side_is_human_readable(self):
        self.assertEqual(_format_ball_side("me"), "Володя")
        self.assertEqual(_format_ball_side("evgeny"), "Евгений")
        self.assertEqual(_format_ball_side("external: Павел"), "Павел")


if __name__ == "__main__":
    unittest.main()
