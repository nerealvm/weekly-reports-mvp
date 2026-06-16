import csv
import tempfile
import unittest
from pathlib import Path

from weekly_assistant.domain.enums import MovementType
from weekly_assistant.services.active_sheet import (
    ACTIVE_SOURCE_FORMAT,
    SERVICE_FIELD_HEADERS,
    active_append_row_values,
    active_row_updates,
    build_active_session_rows_from_csv,
    build_active_week_column_requests,
    column_letter,
    ensure_active_week_columns,
    is_active_sheet_csv,
)


class ActiveSheetTest(unittest.TestCase):
    def test_builds_session_from_active_sheet_and_assigns_sequential_ids(self):
        path = _write_csv(_active_rows())

        rows, raw_rows, metadata = build_active_session_rows_from_csv(path, week_label="15.05")

        self.assertTrue(is_active_sheet_csv(path))
        self.assertEqual(metadata["source_format"], ACTIVE_SOURCE_FORMAT)
        self.assertEqual([row["topic_id"] for row in rows], ["T-001", "T-002"])
        self.assertEqual(rows[0]["row_number"], 4)
        self.assertEqual(rows[0]["topic_title"], "Отчетность")
        self.assertEqual(rows[0]["previous_week_result"], "Результат 8.05")
        self.assertEqual(rows[0]["final_result"], "")
        self.assertEqual(rows[0]["next_milestone"], "15.05 Следующий шаг")
        self.assertEqual(rows[0]["next_milestone_date"], "15.05")
        self.assertEqual(rows[1]["section"], "Задачи")
        self.assertEqual(raw_rows[0]["Topic ID"], "T-001")

    def test_existing_week_reads_current_result_and_previous_result_from_prior_week(self):
        path = _write_csv(_active_rows())

        rows, _raw_rows, _metadata = build_active_session_rows_from_csv(path, week_label="8.05")

        self.assertEqual(rows[0]["previous_week_result"], "Предыдущий статус 8.05")
        self.assertEqual(rows[0]["final_result"], "Результат 8.05")
        self.assertEqual(rows[0]["movement_type"], MovementType.REAL_RESULT.value)

    def test_build_week_column_requests_inserts_all_week_groups(self):
        header_values = [_active_rows()[0], _active_rows()[1]]

        requests, created = build_active_week_column_requests(_FakeSheetsAdapter(), "Активные", header_values, "15.05")

        self.assertEqual(created, ["previous_status", "current_result", "milestone", "movement_type", "needs_sync", "sync_reason"])
        insertions = [request["insertDimension"]["range"]["startIndex"] for request in requests if "insertDimension" in request]
        self.assertEqual(insertions, [14, 12, 9, 6])
        hidden_ranges = [request["updateDimensionProperties"]["range"] for request in requests if "updateDimensionProperties" in request]
        self.assertEqual(hidden_ranges[0]["startIndex"], 14)
        self.assertEqual(hidden_ranges[0]["endIndex"], 17)

    def test_ensure_active_week_columns_resolves_inserted_columns(self):
        adapter = _FakeSheetsAdapter()

        columns, created = ensure_active_week_columns(adapter, target_sheet="Активные", week_label="15.05")

        self.assertEqual(created, ("previous_status", "current_result", "milestone", "movement_type", "needs_sync", "sync_reason"))
        self.assertEqual(column_letter(columns.previous_status_col), "G")
        self.assertEqual(column_letter(columns.current_result_col), "K")
        self.assertEqual(column_letter(columns.milestone_col), "O")
        self.assertEqual(column_letter(columns.movement_col), "R")
        self.assertEqual(column_letter(columns.needs_sync_col), "S")
        self.assertEqual(column_letter(columns.sync_reason_col), "T")

    def test_existing_service_columns_are_hidden_without_recreating(self):
        rows = _active_rows_with_service()

        requests, created = build_active_week_column_requests(_FakeSheetsAdapter(), "Активные", [rows[0], rows[1]], "8.05")

        self.assertEqual(created, [])
        self.assertFalse(any("insertDimension" in request for request in requests))
        hidden_ranges = [request["updateDimensionProperties"]["range"] for request in requests if "updateDimensionProperties" in request]
        self.assertEqual([(item["startIndex"], item["endIndex"]) for item in hidden_ranges], [(14, 15), (15, 16), (16, 17)])

    def test_reads_existing_service_columns_from_active_sheet(self):
        path = _write_csv(_active_rows_with_service())

        rows, _raw_rows, _metadata = build_active_session_rows_from_csv(path, week_label="8.05")

        self.assertEqual(rows[0]["movement_type"], "no_movement")
        self.assertEqual(rows[0]["needs_sync"], "yes")
        self.assertEqual(rows[0]["sync_reason"], "Нужна проверка")

    def test_active_updates_copy_previous_status_and_changed_outputs(self):
        path = _write_csv(_active_rows())
        rows, _raw_rows, metadata = build_active_session_rows_from_csv(path, week_label="15.05")
        columns = ensure_active_week_columns(_FakeSheetsAdapter(), target_sheet="Активные", week_label="15.05")[0]
        row = rows[0]
        row["final_result"] = "Готовый итог"
        row["next_milestone"] = "22.05 Новый шаг"
        row["ball_side"] = "evgeny"
        row["open_question_to_evgeny"] = "Подтверждаем?"
        row["movement_type"] = "real_result"
        row["needs_sync"] = "yes"
        row["sync_reason"] = "Есть вопрос"
        row["changed"] = True
        row["changed_fields"] = ["final_result", "next_milestone", "ball_side", "open_question_to_evgeny", "movement_type", "needs_sync", "sync_reason"]

        updates = active_row_updates(row, columns)

        self.assertEqual((columns.previous_status_col, "Результат 8.05", "previous_week_result"), updates[0])
        self.assertIn((columns.current_result_col, "Готовый итог", "final_result"), updates)
        self.assertIn((columns.milestone_col, "22.05 Новый шаг", "next_milestone"), updates)
        self.assertIn((columns.ball_col, "evgeny", "ball_side"), updates)
        self.assertIn((columns.question_col, "Подтверждаем?", "open_question_to_evgeny"), updates)
        self.assertIn((columns.movement_col, "real_result", "movement_type"), updates)
        self.assertIn((columns.needs_sync_col, "yes", "needs_sync"), updates)
        self.assertIn((columns.sync_reason_col, "Есть вопрос", "sync_reason"), updates)
        self.assertEqual(metadata["active_sheet"]["week_label"], "15.05")

    def test_active_append_row_values_uses_active_columns(self):
        columns = ensure_active_week_columns(_FakeSheetsAdapter(), target_sheet="Активные", week_label="15.05")[0]
        values = active_append_row_values(
            {
                "topic_title": "Новая тема",
                "date_created": "15.05",
                "previous_week_result": "Было",
                "final_result": "Сделали",
                "next_milestone": "22.05 Шаг",
                "ball_side": "me",
                "open_question_to_evgeny": "",
                "movement_type": "real_result",
                "needs_sync": "no",
                "sync_reason": "",
            },
            columns,
        )

        self.assertEqual(values[columns.topic_col - 1], "Новая тема")
        self.assertEqual(values[columns.previous_status_col - 1], "Было")
        self.assertEqual(values[columns.current_result_col - 1], "Сделали")
        self.assertEqual(values[columns.movement_col - 1], "real_result")
        self.assertEqual(values[columns.needs_sync_col - 1], "no")


def _active_rows() -> list[list[str]]:
    return [
        [
            "",
            "Тема",
            "Дата постановки",
            "Статус предыдущей недели",
            "",
            "",
            "Куда мы докатились на этой",
            "",
            "",
            "Когда докатимся и куда",
            "",
            "",
            "На чьей стороне мяч",
            "Открытые вопросы",
        ],
        ["", "", "", "", "1.05", "8.05", "", "1.05", "8.05", "", "1.05", "8.05", "", ""],
        ["Проекты"],
        ["", "Отчетность", "01.05", "", "Предыдущий статус 1.05", "Предыдущий статус 8.05", "", "Результат 1.05", "Результат 8.05", "", "10.05 Старый шаг", "15.05 Следующий шаг", "me", ""],
        ["Задачи"],
        ["", "Звонок", "02.05", "", "", "", "", "", "Без нового движения за неделю.", "", "", "", "evgeny", "Что решаем?"],
    ]


def _active_rows_with_service() -> list[list[str]]:
    rows = [list(row) for row in _active_rows()]
    for _field, header in SERVICE_FIELD_HEADERS:
        rows[0].append(header)
        rows[1].append("")
    rows[3].extend(["no_movement", "yes", "Нужна проверка"])
    rows[5].extend(["unclear", "no", ""])
    return rows


def _write_csv(rows: list[list[str]]) -> Path:
    tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False)
    with tmp:
        writer = csv.writer(tmp)
        writer.writerows(rows)
    return Path(tmp.name)


class _FakeSheetsAdapter:
    def __init__(self):
        self.batch_requests = []

    def read_values(self, _range):
        if self.batch_requests:
            return [
                ["", "Тема", "Дата постановки", "Статус предыдущей недели", "", "", "", "Куда мы докатились на этой", "", "", "", "Когда докатимся и куда", "", "", "", "На чьей стороне мяч", "Открытые вопросы", "Movement type", "Нужен sync", "Причина sync"],
                ["", "", "", "", "1.05", "8.05", "15.05", "", "1.05", "8.05", "15.05", "", "1.05", "8.05", "15.05", "", "", "", "", ""],
            ]
        return [_active_rows()[0], _active_rows()[1]]

    def batch_update(self, requests):
        self.batch_requests.extend(requests)
        return {}

    def sheet_properties(self):
        return [{"title": "Активные", "sheetId": 123}]


if __name__ == "__main__":
    unittest.main()
