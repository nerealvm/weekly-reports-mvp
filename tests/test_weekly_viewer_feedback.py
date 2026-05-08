import tempfile
import unittest
from pathlib import Path

from weekly_assistant.config.settings import Settings
from weekly_assistant.viewer_server import FEEDBACK_HEADERS, WeeklyViewerStore, _comments_from_sheet_values, _feedback_summary, _safe_filename


def _settings() -> Settings:
    return Settings(
        google_sheets_spreadsheet_id="",
        google_sheets_weekly_gid="",
        google_sheets_weekly_sheet_name="",
        google_oauth_access_token="",
        google_application_credentials="",
        telegram_bot_token="",
        telegram_api_id="",
        telegram_api_hash="",
        telegram_session_name="",
        singularity_api_token="",
        singularity_base_url="",
        singularity_project_id="",
        singularity_projects_config="",
        openai_api_key="",
        openai_model="",
    )


class WeeklyViewerFeedbackTest(unittest.TestCase):
    def test_feedback_summary_counts_open_comments_by_topic_and_field(self):
        payload = _feedback_summary(
            [
                {"topic_id": "T-001", "field": "result", "status": "open"},
                {"topic_id": "T-001", "field": "result", "status": "open"},
                {"topic_id": "T-001", "field": "milestones", "status": "resolved"},
                {"topic_id": "T-002", "field": "ball_side"},
            ]
        )

        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["by_key"]["T-001:result"], 2)
        self.assertEqual(payload["by_topic"]["T-001"], 2)
        self.assertEqual(payload["by_topic"]["T-002"], 1)

    def test_add_comment_persists_feedback_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "feedback.json"
            store = WeeklyViewerStore(
                _settings(),
                sheet_name="Weekly MVP 2026-05-08",
                week_label="08.05",
                feedback_path=path,
                async_sheet_sync=False,
            )

            result = store.add_comment(
                {
                    "topic_id": "T-001",
                    "topic_title": "AI-контур weekly",
                    "field": "result",
                    "text": "Что именно было принято?",
                }
            )

            self.assertTrue(path.exists())
            self.assertEqual(result["feedback"]["count"], 1)
            self.assertEqual(store.feedback()["by_key"]["T-001:result"], 1)

    def test_add_comment_appends_to_feedback_sheet_when_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = FakeSheetsAdapter()
            path = Path(temp_dir) / "feedback.json"
            store = WeeklyViewerStore(
                _settings(),
                sheet_name="Weekly MVP 2026-05-08",
                week_label="08.05",
                feedback_path=path,
                sheets_adapter_factory=lambda _settings: adapter,
                async_sheet_sync=False,
            )

            result = store.add_comment(
                {
                    "topic_id": "T-001",
                    "topic_title": "AI-контур weekly",
                    "field": "result",
                    "text": "Что именно было принято?",
                }
            )

            self.assertEqual(result["sheet"]["status"], "saved")
            self.assertTrue(adapter.created_sheet)
            self.assertEqual(adapter.rows[0][0], result["comment"]["id"])
            self.assertEqual(adapter.rows[0][8], "Что именно было принято?")
            self.assertEqual(result["feedback"]["by_key"]["T-001:result"], 1)

    def test_comments_from_sheet_values_parses_open_comments(self):
        comments = _comments_from_sheet_values(
            [
                FEEDBACK_HEADERS,
                ["C-1", "2026-05-08T10:00:00+03:00", "08.05", "Weekly", "T-001", "Тема", "result", "Результат", "Вопрос", "Евгений", "open"],
            ]
        )

        self.assertEqual(comments[0]["id"], "C-1")
        self.assertEqual(comments[0]["text"], "Вопрос")
        self.assertEqual(comments[0]["sheet_sync"], "saved")

    def test_safe_filename_keeps_sheet_name_readable(self):
        self.assertEqual(_safe_filename("Weekly MVP 2026-05-08"), "Weekly_MVP_2026-05-08")


class FakeSheetsAdapter:
    def __init__(self):
        self.created_sheet = False
        self.header: list[str] = []
        self.rows: list[list[str]] = []

    def spreadsheet_metadata(self) -> dict:
        sheets = [{"properties": {"title": "Weekly Feedback"}}] if self.created_sheet else []
        return {"sheets": sheets}

    def batch_update(self, requests: list[dict]) -> dict:
        self.created_sheet = any("addSheet" in request for request in requests)
        return {}

    def update_values(self, a1_range: str, values: list[list[str]], value_input_option: str = "USER_ENTERED") -> dict:
        self.header = values[0]
        return {}

    def append_values(
        self,
        a1_range: str,
        values: list[list[str]],
        value_input_option: str = "USER_ENTERED",
        insert_data_option: str = "INSERT_ROWS",
    ) -> dict:
        self.rows.extend(values)
        return {}

    def read_values(self, a1_range: str) -> list[list[str]]:
        if a1_range.endswith("A1:K1"):
            return [self.header] if self.header else []
        if a1_range.endswith("A:K"):
            return ([self.header] if self.header else []) + self.rows
        return []


if __name__ == "__main__":
    unittest.main()
