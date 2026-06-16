import unittest

from weekly_assistant.services.legacy_active_transfer import (
    LegacyTransferColumns,
    build_legacy_transfer_items,
    build_legacy_week_column_requests,
    column_index,
    column_letter,
    ensure_legacy_rows,
    preview_legacy_week_columns,
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

    def test_preview_week_columns_adds_missing_labels(self):
        first = ["", "Тема", "Дата постановки", "Куда мы докатились на этой", "", "Когда докатимся и куда", "", "На чьей стороне мяч"]
        second = ["", "", "", "", "8.05", "", "8.05", ""]

        values, created = preview_legacy_week_columns([first, second], "15.05")
        columns = resolve_legacy_columns(values, week_label="15.05")

        self.assertEqual(created, ("status", "milestone"))
        self.assertEqual(column_letter(columns.status_col), "F")
        self.assertEqual(column_letter(columns.milestone_col), "I")

    def test_build_week_column_requests_inserts_before_next_groups(self):
        first = ["", "Тема", "Дата постановки", "Куда мы докатились на этой", "", "Когда докатимся и куда", "", "На чьей стороне мяч"]
        second = ["", "", "", "", "8.05", "", "8.05", ""]

        requests, created = build_legacy_week_column_requests(_FakeSheetsAdapter(), "Активные", [first, second], "15.05")

        self.assertEqual(created, ["status", "milestone"])
        self.assertEqual(requests[0]["insertDimension"]["range"]["startIndex"], 7)
        self.assertEqual(requests[2]["insertDimension"]["range"]["startIndex"], 5)

    def test_ensure_legacy_rows_appends_missing_active_topics_and_updates_source(self):
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
            ["Проекты", "T-010", "Новая тема", "15.05", "active", "no", "Было", "", "", "Сделали", "Шаг", "22.05", "Я", "Вопрос?", "", "", "", "", "", ""],
        ]
        target_headers = [
            ["", "Тема", "Дата постановки", "Статус предыдущей недели", "Куда мы докатились на этой", "", "Когда докатимся и куда", "", "На чьей стороне мяч", "Открытые вопросы"],
            ["", "", "", "", "", "15.05", "", "15.05", "", ""],
        ]
        adapter = _FakeSheetsAdapter()

        updated_source, created = ensure_legacy_rows(
            adapter,
            source_sheet="Weekly MVP 2026-05-15",
            target_sheet="Активные",
            source_values=source,
            target_header_values=target_headers,
        )

        self.assertEqual(created, (("T-010", "Новая тема", 31),))
        self.assertEqual(updated_source[1][-1], "31")
        self.assertEqual(adapter.appends[0][0], "'Активные'!A:ZZ")
        self.assertEqual(adapter.appends[0][1][0][1], "Новая тема")
        self.assertEqual(adapter.updates[0], ("'Weekly MVP 2026-05-15'!T2", [["31"]]))

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


class _FakeSheetsAdapter:
    def __init__(self):
        self.appends = []
        self.updates = []

    def sheet_properties(self):
        return [{"title": "Активные", "sheetId": 123}]

    def append_values(self, a1_range, values):
        self.appends.append((a1_range, values))
        return {"updates": {"updatedRange": "'Активные'!A31:ZZ31"}}

    def update_values(self, a1_range, values):
        self.updates.append((a1_range, values))
        return {"updatedRange": a1_range}


if __name__ == "__main__":
    unittest.main()
