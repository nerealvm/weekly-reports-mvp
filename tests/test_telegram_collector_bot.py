import unittest
from datetime import date

from weekly_assistant import telegram_collector_bot as bot
from weekly_assistant.utils.weeks import current_week_bounds


class WeekBoundsTest(unittest.TestCase):
    def test_end_is_always_a_friday(self):
        for day in range(1, 29):
            _, end = current_week_bounds(date(2026, 9, day))
            self.assertEqual(end.weekday(), 4, f"2026-09-{day} gave non-Friday end {end}")

    def test_friday_maps_to_itself_not_next_week(self):
        start, end = current_week_bounds(date(2026, 9, 11))
        self.assertEqual((start, end), (date(2026, 9, 4), date(2026, 9, 11)))

    def test_window_is_seven_days(self):
        start, end = current_week_bounds(date(2026, 9, 10))
        self.assertEqual((end - start).days, 7)


class _FakeAdapter:
    def __init__(self, payload=b'{"items": []}'):
        self.sent = []
        self._payload = payload

    def send_message(self, chat_id, text):
        self.sent.append(text)
        return {}

    def get_file(self, file_id):
        return {"result": {"file_path": "doc.json"}}

    def download_file(self, file_path, **kwargs):
        return self._payload


class _Args:
    allowed_user_id = "777"
    sheet_name = "Активные"
    gid = "0"
    spreadsheet_id = ""
    session_dir = "/tmp/does-not-exist"


def _message(**overrides):
    message = {"chat": {"id": 1}, "from": {"id": 777}}
    message.update(overrides)
    return message


class BotGuardsTest(unittest.TestCase):
    def test_message_from_other_user_is_ignored(self):
        adapter = _FakeAdapter()
        bot.handle_message(adapter, None, _Args(), {"chat": {"id": 1}, "from": {"id": 999}, "text": "/status"})
        self.assertEqual(adapter.sent, [])

    def test_non_json_attachment_is_rejected(self):
        adapter = _FakeAdapter()
        bot.handle_message(adapter, None, _Args(), _message(document={"file_name": "scan.pdf", "file_id": "f"}))
        self.assertIn("Нужен .json", adapter.sent[0])

    def test_malformed_json_never_reaches_the_sheet(self):
        adapter = _FakeAdapter()
        bot.handle_message(adapter, None, _Args(), _message(text="{oops"))
        self.assertIn("не валидный JSON", adapter.sent[0])
        self.assertFalse(any("Принял" in text for text in adapter.sent))

    def test_plain_text_gets_help_instead_of_an_import(self):
        adapter = _FakeAdapter()
        bot.handle_message(adapter, None, _Args(), _message(text="привет"))
        self.assertIn("JSON-файл", adapter.sent[0])

    def test_fenced_json_document_is_accepted_as_payload(self):
        adapter = _FakeAdapter(payload=b'```json\n{"items": []}\n```')
        payload = bot.extract_payload(adapter, _message(document={"file_name": "w.json", "file_id": "f"}))
        self.assertIn("items", payload)


if __name__ == "__main__":
    unittest.main()


class RowResolutionTest(unittest.TestCase):
    """A session row_number comes from the CSV export, which drops rows and so
    drifts from the physical sheet row. It must never address a write."""

    def test_title_map_ignores_duplicate_titles(self):
        from weekly_assistant.collector_server import _read_topic_title_map

        class Adapter:
            def read_values(self, a1):
                return [["Тема"], ["Альфа"], ["Бета"], ["Альфа"]]

        class Cols:
            topic_col = 3

        got = _read_topic_title_map(Adapter(), "Активные", Cols())
        self.assertIn("бета", got)
        self.assertEqual(got["бета"], 3)
        self.assertNotIn("альфа", got, "duplicate titles must not resolve to a row")

    def test_title_map_is_empty_without_a_title_column(self):
        from weekly_assistant.collector_server import _read_topic_title_map

        class Cols:
            topic_col = None

        self.assertEqual(_read_topic_title_map(object(), "Активные", Cols()), {})
