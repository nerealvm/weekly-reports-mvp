import unittest

from weekly_assistant.collector_server import (
    _apply_chatgpt_import,
    _build_row_prompt,
    _build_chatgpt_context,
    _draft_open_question,
    _format_milestones_for_excel,
    _format_milestones_text,
    _loosely_matches,
    _parse_milestones_text,
    _writeback_value,
)
from weekly_assistant.domain.enums import MovementType, ReviewStatus, YesNo


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

    def test_chatgpt_context_contains_active_topics_and_schema(self):
        session = {
            "metadata": {"week_start": "2026-05-01", "week_end": "2026-05-07"},
            "rows": [
                {"topic_id": "T-001", "topic_title": "Активная", "lifecycle": "active", "focus": "no"},
                {"topic_id": "T-002", "topic_title": "Пауза", "lifecycle": "paused", "focus": "no"},
            ],
        }

        context = _build_chatgpt_context(session)

        self.assertEqual(context["week_start"], "2026-05-01")
        self.assertEqual([item["topic_id"] for item in context["topics"]], ["T-001"])
        self.assertIn("items", context["output_schema"])


if __name__ == "__main__":
    unittest.main()
