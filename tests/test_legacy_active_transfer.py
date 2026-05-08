import unittest

from weekly_assistant.services.legacy_active_transfer import (
    LegacyTransferColumns,
    build_legacy_transfer_items,
    column_index,
    column_letter,
    resolve_legacy_columns,
)


class LegacyActiveTransferTest(unittest.TestCase):
    def test_column_letter_roundtrip(self):
        for index, letter in [(1, "A"), (26, "Z"), (27, "AA"), (29, "AC"), (41, "AO")]:
            self.assertEqual(column_letter(index), letter)
            self.assertEqual(column_index(letter), index)

    def test_resolve_columns_uses_rightmost_week_label_by_default(self):
        first = ["", "Тема", "Дата постановки", "Статус предыдущей недели", "", "", "Куда мы докатились на этой", "", "", "Когда докатимся и куда", "", "", "На чьей стороне мяч", "Открытые вопросы"]
        second = ["", "", "", "", "1.05", "8.05", "", "1.05", "8.05", "", "1.05", "8.05", "", ""]

        columns = resolve_legacy_columns([first, second])

        self.assertEqual(column_letter(columns.status_col), "I")
        self.assertEqual(column_letter(columns.milestone_col), "L")
        self.assertEqual(column_letter(columns.ball_col), "M")
        self.assertIsNone(columns.question_col)

    def test_resolve_columns_can_include_questions(self):
        first = ["", "Тема", "Дата постановки", "Куда мы докатились на этой", "", "Когда докатимся и куда", "", "На чьей стороне мяч", "Открытые вопросы"]
        second = ["", "", "", "", "8.05", "", "8.05", "", ""]

        columns = resolve_legacy_columns([first, second], include_open_questions=True)

        self.assertEqual(column_letter(columns.question_col), "I")

    def test_build_transfer_items_uses_legacy_row_and_formats_milestone_date(self):
        source = [
            [
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
            ["Проекты", "T-001", "Тема", "", "active", "no", "", "", "", "Сделали результат", "Получить ответ", "15.05", "Евгений", "", "", "", "", "", "", "4"],
        ]
        columns = LegacyTransferColumns(status_col=29, milestone_col=41, ball_col=42, question_col=None, status_label="8.05", milestone_label="8.05")

        items, skipped = build_legacy_transfer_items(source, columns)

        self.assertEqual(skipped, [])
        self.assertEqual(items[0].legacy_row, 4)
        self.assertEqual(items[0].updates, ((29, "Сделали результат"), (41, "15.05 Получить ответ"), (42, "Евгений")))

    def test_build_transfer_items_does_not_duplicate_month_year_milestone_date(self):
        source = [
            [
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
            ["Проекты", "T-007", "Рыбоводство", "", "active", "no", "", "", "", "Без движения", "07.2026 Собрать воронку", "7.2026", "Володя", "", "", "", "", "", "", "10"],
        ]
        columns = LegacyTransferColumns(status_col=29, milestone_col=41, ball_col=42, question_col=None, status_label="8.05", milestone_label="8.05")

        items, skipped = build_legacy_transfer_items(source, columns)

        self.assertEqual(skipped, [])
        self.assertEqual(items[0].updates[1], (41, "07.2026 Собрать воронку"))

    def test_build_transfer_items_skips_non_active_rows(self):
        source = [
            [
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
            ["На паузе", "T-025", "Пауза", "", "paused", "no", "", "", "", "Не трогали", "", "", "На паузе", "", "", "", "", "", "", "33"],
        ]
        columns = LegacyTransferColumns(status_col=29, milestone_col=41, ball_col=42, question_col=None, status_label="8.05", milestone_label="8.05")

        items, skipped = build_legacy_transfer_items(source, columns)

        self.assertEqual(items, [])
        self.assertIn("lifecycle=paused", skipped[0])


if __name__ == "__main__":
    unittest.main()
