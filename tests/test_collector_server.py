import unittest
import tempfile
import threading
from datetime import date
from pathlib import Path
from unittest.mock import patch

from weekly_assistant.collector_server import (
    CollectorConfig,
    CollectorStore,
    _apply_chatgpt_import,
    _build_row_prompt,
    _build_chatgpt_context,
    _draft_open_question,
    _format_milestones_for_excel,
    _format_milestones_text,
    _loosely_matches,
    _default_legacy_week_label,
    _next_friday_report_window,
    _next_topic_id,
    _parse_milestones_from_row,
    _parse_milestones_text,
    _project_mode_patch,
    _weekly_sheet_title_for_session,
    _writeback_value,
)
from weekly_assistant.config.settings import Settings
from weekly_assistant.domain.enums import Lifecycle, MovementType, ReviewStatus, YesNo


class CollectorServerTest(unittest.TestCase):
    def test_loose_matching_handles_punctuation(self):
        self.assertTrue(_loosely_matches("Рыбоводство (РФ)", "Рыбоводство РФ"))

    def test_writeback_value_normalizes_enums(self):
        self.assertEqual(_writeback_value({"movement_type": "bad"}, "movement_type"), MovementType.UNCLEAR.value)
        self.assertEqual(_writeback_value({"needs_sync": "bad"}, "needs_sync"), YesNo.NO.value)
        self.assertEqual(_writeback_value({"review_status": "bad"}, "review_status"), ReviewStatus.DRAFT.value)

    def test_row_prompt_does_not_use_previous_week_or_imported_facts(self):
        prompt = _build_row_prompt(
            {
                "topic_title": "ТЭЦ",
                "previous_week_result": "Прошлая неделя: отправили письмо.",
                "current_week_facts": "Старый перенесенный факт.",
                "raw_fact": "",
                "open_question_to_evgeny": "Старый вопрос к Евгению?",
                "next_milestone": "Согласовать шаг",
                "next_milestone_date": "2026-05-15",
                "ball_side": "Я",
                "hints": [],
            }
        )

        self.assertNotIn("Прошлая неделя: отправили письмо.", prompt)
        self.assertNotIn("Старый перенесенный факт.", prompt)
        self.assertNotIn("Старый вопрос к Евгению?", prompt)

    def test_row_prompt_uses_manual_current_facts(self):
        prompt = _build_row_prompt(
            {
                "topic_title": "ТЭЦ",
                "current_week_facts": "Я руками внес факт этой недели.",
                "manual_current_week_facts": True,
                "raw_fact": "",
                "hints": [],
            }
        )

        self.assertIn("Я руками внес факт этой недели.", prompt)

    def test_open_question_requires_explicit_current_signal(self):
        row = {
            "raw_fact": "Созвонились с юристами, следующий шаг на мне.",
            "open_question_to_evgeny": "Старый вопрос?",
        }

        question = _draft_open_question(row, {"open_question_to_evgeny": "Новый выдуманный вопрос?"})

        self.assertEqual(question, "")

    def test_open_question_allows_manual_field(self):
        row = {
            "raw_fact": "Созвонились с юристами.",
            "open_question_to_evgeny": "Вопрос к Евгению: подтверждаем бюджет?",
            "manual_open_question_to_evgeny": True,
        }

        question = _draft_open_question(row, {"open_question_to_evgeny": ""})

        self.assertEqual(question, "Вопрос к Евгению: подтверждаем бюджет?")

    def test_milestones_parse_and_format_for_excel(self):
        milestones = _parse_milestones_text("10.05 Отправить проект\n15.05 Получить комментарии")

        self.assertEqual(milestones[0], {"date": "10.05", "text": "Отправить проект"})
        self.assertEqual(_format_milestones_text(milestones), "10.05 Отправить проект\n15.05 Получить комментарии")
        self.assertEqual(_format_milestones_for_excel(milestones), "10.05 Отправить проект; 15.05 Получить комментарии")

    def test_milestones_from_row_does_not_duplicate_date_prefix(self):
        milestones = _parse_milestones_from_row("30.05 обновленная концепция", "30.05")

        self.assertEqual(milestones, [{"date": "30.05", "text": "обновленная концепция"}])

    def test_chatgpt_import_updates_row_and_milestones(self):
        session = {
            "rows": [
                {
                    "topic_id": "T-001",
                    "topic_title": "Квадрига",
                    "lifecycle": "active",
                    "current_week_facts": "",
                    "open_question_to_evgeny": "",
                }
            ]
        }
        payload = """
        {
          "items": [
            {
              "topic_id": "T-001",
              "final_result": "Передали данные оценщику.",
              "milestones": [
                {"date": "10.05", "text": "Получить оценку"},
                {"date": "15.05", "text": "Согласовать договор"}
              ],
              "ball_side": "external: оценщик",
              "movement_type": "real_result",
              "needs_sync": "no"
            }
          ]
        }
        """

        result = _apply_chatgpt_import(session, payload)
        row = session["rows"][0]

        self.assertEqual(result["imported_count"], 1)
        self.assertEqual(row["final_result"], "Передали данные оценщику.")
        self.assertEqual(row["next_milestone"], "10.05 Получить оценку; 15.05 Согласовать договор")
        self.assertEqual(row["next_milestone_date"], "10.05")
        self.assertEqual(row["status"], "chatgpt_imported")

    def test_chatgpt_import_does_not_clear_existing_ball_side_when_blank(self):
        session = {
            "rows": [
                {
                    "topic_id": "T-001",
                    "topic_title": "Квадрига",
                    "lifecycle": "active",
                    "current_week_facts": "",
                    "open_question_to_evgeny": "",
                    "ball_side": "me",
                    "changed_fields": [],
                }
            ]
        }

        _apply_chatgpt_import(session, '{"items":[{"topic_id":"T-001","final_result":"Не двигал.","ball_side":""}]}')

        self.assertEqual(session["rows"][0]["ball_side"], "me")
        self.assertNotIn("ball_side", session["rows"][0]["changed_fields"])

    def test_chatgpt_context_contains_active_topics_and_schema(self):
        session = {
            "metadata": {"week_start": "2026-05-01", "week_end": "2026-05-07"},
            "rows": [
                {
                    "topic_id": "T-001",
                    "topic_title": "Активная",
                    "lifecycle": "active",
                    "focus": "no",
                    "open_question_to_evgeny": "Старый вопрос?",
                },
                {"topic_id": "T-002", "topic_title": "Пауза", "lifecycle": "paused", "focus": "no"},
            ],
        }

        context = _build_chatgpt_context(session)

        self.assertEqual(context["week_start"], "2026-05-01")
        self.assertEqual([item["topic_id"] for item in context["topics"]], ["T-001"])
        self.assertEqual(context["topics"][0]["old_open_question_to_evgeny"], "Старый вопрос?")
        self.assertIn("оставлять ли этот вопрос актуальным", " ".join(context["instructions"]))
        self.assertIn("items", context["output_schema"])

    def test_project_mode_patch_maps_hold_to_paused(self):
        self.assertEqual(_project_mode_patch("task"), {"section": "Задачи", "lifecycle": "active"})
        self.assertEqual(_project_mode_patch("на холде"), {"section": "На Паузе", "lifecycle": "paused"})

    def test_next_topic_id_uses_next_numeric_suffix(self):
        session = {"rows": [{"topic_id": "T-001"}, {"topic_id": "T-099"}, {"topic_id": "misc"}]}

        self.assertEqual(_next_topic_id(session), "T-100")

    def test_create_project_adds_new_changed_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store_for_session(_base_session(), Path(tmp))

            row = store.create_project({"topic_title": "Новая задача", "mode": "task"})

            self.assertEqual(row["topic_id"], "T-003")
            self.assertEqual(row["section"], "Задачи")
            self.assertEqual(row["lifecycle"], Lifecycle.ACTIVE.value)
            self.assertTrue(row["is_new"])
            self.assertTrue(row["changed"])

    def test_start_next_week_carries_result_and_clears_weekly_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store_for_session(_base_session(), Path(tmp))

            result = store.start_next_week("2026-05-08", "2026-05-14")
            row = store.load()["rows"][0]

            self.assertEqual(result["updated_count"], 1)
            self.assertEqual(row["previous_week_result"], "Готовый итог")
            self.assertEqual(row["final_result"], "")
            self.assertEqual(row["current_week_facts"], "")
            self.assertEqual(row["movement_type"], MovementType.UNCLEAR.value)
            self.assertTrue(row["changed"])
            self.assertIn("previous_week_result", row["changed_fields"])

    def test_next_week_auto_uses_friday_to_friday_window_from_legacy_thursday_end(self):
        start, end = _next_friday_report_window({"metadata": {"week_start": "2026-05-08", "week_end": "2026-05-14"}})

        self.assertEqual(start, date(2026, 5, 15))
        self.assertEqual(end, date(2026, 5, 22))

    def test_next_week_auto_reuses_friday_boundary_as_next_start(self):
        start, end = _next_friday_report_window({"metadata": {"week_start": "2026-05-08", "week_end": "2026-05-15"}})

        self.assertEqual(start, date(2026, 5, 15))
        self.assertEqual(end, date(2026, 5, 22))

    def test_start_next_week_without_dates_uses_friday_to_friday_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store_for_session(_base_session(), Path(tmp))

            result = store.start_next_week()

            self.assertEqual(result["week_start"], "2026-05-08")
            self.assertEqual(result["week_end"], "2026-05-15")

    def test_weekly_sheet_title_uses_report_window_end(self):
        session = {"metadata": {"week_end": "2026-05-22"}}

        self.assertEqual(_weekly_sheet_title_for_session(session), "Weekly MVP 2026-05-22")

    def test_default_legacy_week_label_prefers_sheet_date(self):
        session = {"sheet_name": "Weekly MVP 2026-05-15", "metadata": {"week_end": "2026-05-22"}}

        self.assertEqual(_default_legacy_week_label(session), "15.05")

    def test_write_back_updates_full_range_for_structural_changes_and_appends_new_rows(self):
        session = _base_session()
        session["rows"][0]["topic_title"] = "Новое имя"
        session["rows"][0]["changed"] = True
        session["rows"][0]["changed_fields"] = ["topic_title"]
        with tempfile.TemporaryDirectory() as tmp:
            store = _store_for_session(session, Path(tmp))
            new_row = store.create_project({"topic_title": "Новая строка", "mode": "hold"})

            _FakeSheetsAdapter.instances = []
            with patch("weekly_assistant.collector_server.GoogleSheetsAdapter", _FakeSheetsAdapter):
                result = store.write_back()

            adapter = _FakeSheetsAdapter.instances[0]
            self.assertEqual(result["updated_count"], 2)
            self.assertEqual(adapter.updates[0][0], "'Weekly'!A2:S2")
            self.assertEqual(adapter.appends[0][0], "'Weekly'!A:S")
            saved_new_row = _find_saved_row(store.load(), new_row["topic_id"])
            self.assertFalse(saved_new_row["changed"])
            self.assertFalse(saved_new_row["is_new"])

    def test_create_week_tab_duplicates_target_and_updates_session_sheet(self):
        session = _base_session()
        session["metadata"]["week_end"] = "2026-05-15"
        session["sheet_name"] = "Weekly MVP 2026-05-08"
        with tempfile.TemporaryDirectory() as tmp:
            store = _store_for_session(session, Path(tmp))

            _FakeSheetsAdapter.instances = []
            with patch("weekly_assistant.collector_server.GoogleSheetsAdapter", _FakeSheetsAdapter):
                result = store.create_week_tab()

            adapter = _FakeSheetsAdapter.instances[0]
            self.assertTrue(result["created"])
            self.assertEqual(result["sheet_name"], "Weekly MVP 2026-05-15")
            self.assertEqual(store.load()["sheet_name"], "Weekly MVP 2026-05-15")
            self.assertEqual(adapter.duplicates, [("Weekly MVP 2026-05-08", "Weekly MVP 2026-05-15")])
            self.assertEqual(adapter.updates[0][0], "'Weekly MVP 2026-05-15'!A2:T3")

    def test_create_week_tab_skips_active_source_session(self):
        session = _base_session()
        session["metadata"]["source_format"] = "active_legacy"
        session["metadata"]["active_sheet"] = {"sheet_name": "Активные", "week_label": "15.05"}
        session["sheet_name"] = "Активные"
        session["gid"] = "0"
        with tempfile.TemporaryDirectory() as tmp:
            store = _store_for_session(session, Path(tmp))

            result = store.create_week_tab()

            self.assertFalse(result["created"])
            self.assertTrue(result["skipped"])
            self.assertEqual(result["sheet_name"], "Активные")


def _settings() -> Settings:
    return Settings(
        google_sheets_spreadsheet_id="sheet",
        google_sheets_weekly_gid="gid",
        google_sheets_weekly_sheet_name="Weekly",
        google_oauth_access_token="token",
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


def _store_for_session(session: dict, root: Path) -> CollectorStore:
    store = CollectorStore.__new__(CollectorStore)
    store.settings = _settings()
    store.config = CollectorConfig(
        spreadsheet_id="sheet",
        gid="gid",
        sheet_name="Weekly",
        week_start=date(2026, 5, 1),
        week_end=date(2026, 5, 7),
        session_dir=root,
        source_csv=None,
        refresh=False,
    )
    root.mkdir(parents=True, exist_ok=True)
    store.session_path = root / "session.json"
    store.source_path = root / "source.csv"
    store.candidate_path = root / "candidate.csv"
    store.lock = threading.Lock()
    store.save(session)
    return store


def _base_session() -> dict:
    return {
        "metadata": {"week_start": "2026-05-01", "week_end": "2026-05-07"},
        "spreadsheet_id": "sheet",
        "gid": "gid",
        "sheet_name": "Weekly",
        "csv_fieldnames": [
            "Секция",
            "Topic ID",
            "Тема",
            "Дата постановки",
            "Lifecycle",
            "Focus",
            "Результат прошлой недели",
            "Факты этой недели",
            "AI draft result",
            "Итоговая формулировка",
            "Ближайшая веха",
            "Дата вехи",
            "На чьей стороне мяч",
            "Открытый вопрос к Евгению",
            "Movement type",
            "Нужен sync",
            "Причина sync",
            "Source / links",
            "Review status",
            "Legacy row",
        ],
        "raw_rows": [
            {
                "_row_number": 2,
                "Секция": "Проекты",
                "Topic ID": "T-001",
                "Тема": "Старое имя",
                "Дата постановки": "01.05",
                "Lifecycle": "active",
                "Focus": "no",
                "Результат прошлой недели": "Старый итог",
                "Факты этой недели": "Факт",
                "AI draft result": "",
                "Итоговая формулировка": "Готовый итог",
                "Ближайшая веха": "10.05 Следующий шаг",
                "Дата вехи": "10.05",
                "На чьей стороне мяч": "Я",
                "Открытый вопрос к Евгению": "",
                "Movement type": "real_result",
                "Нужен sync": "no",
                "Причина sync": "",
                "Source / links": "",
                "Review status": "reviewed",
                "Legacy row": "10",
            }
        ],
        "rows": [
            {
                "row_number": 2,
                "topic_id": "T-001",
                "topic_title": "Старое имя",
                "section": "Проекты",
                "date_created": "01.05",
                "lifecycle": "active",
                "focus": "no",
                "previous_week_result": "Старый итог",
                "raw_fact": "",
                "current_week_facts": "Факт",
                "ai_draft_result": "",
                "final_result": "Готовый итог",
                "next_milestone": "10.05 Следующий шаг",
                "next_milestone_date": "10.05",
                "ball_side": "Я",
                "open_question_to_evgeny": "",
                "manual_current_week_facts": False,
                "manual_open_question_to_evgeny": False,
                "milestones": [],
                "milestones_text": "",
                "movement_type": "real_result",
                "needs_sync": "no",
                "sync_reason": "",
                "source_links": "",
                "review_status": "reviewed",
                "status": "has_facts",
                "changed": False,
                "changed_fields": [],
                "is_new": False,
                "hints": [],
            },
            {
                "row_number": 3,
                "topic_id": "T-002",
                "topic_title": "Архив",
                "section": "Архив",
                "date_created": "01.05",
                "lifecycle": "archived",
                "focus": "no",
                "previous_week_result": "",
                "raw_fact": "",
                "current_week_facts": "",
                "ai_draft_result": "",
                "final_result": "",
                "next_milestone": "",
                "next_milestone_date": "",
                "ball_side": "",
                "open_question_to_evgeny": "",
                "manual_current_week_facts": False,
                "manual_open_question_to_evgeny": False,
                "milestones": [],
                "milestones_text": "",
                "movement_type": "unclear",
                "needs_sync": "no",
                "sync_reason": "",
                "source_links": "",
                "review_status": "draft",
                "status": "empty",
                "changed": False,
                "changed_fields": [],
                "is_new": False,
                "hints": [],
            },
        ],
    }


def _find_saved_row(session: dict, topic_id: str) -> dict:
    for row in session["rows"]:
        if row["topic_id"] == topic_id:
            return row
    raise AssertionError(topic_id)


class _FakeSheetsAdapter:
    instances = []

    def __init__(self, settings):
        self.settings = settings
        self.updates = []
        self.appends = []
        self.duplicates = []
        self.__class__.instances.append(self)

    def update_values(self, a1_range, values):
        self.updates.append((a1_range, values))
        return {"updatedRange": a1_range}

    def append_values(self, a1_range, values):
        self.appends.append((a1_range, values))
        return {"updates": {"updatedRange": "'Weekly'!A4:S4"}}

    def sheet_properties(self):
        return [
            {"title": "Weekly MVP 2026-05-08", "sheetId": 100, "index": 1},
            {"title": "Weekly", "sheetId": 101, "index": 2},
        ]

    def duplicate_sheet(self, source_title, new_title):
        self.duplicates.append((source_title, new_title))
        return {"skipped": False, "properties": {"title": new_title, "sheetId": 200, "index": 2}, "result": {}}


if __name__ == "__main__":
    unittest.main()
