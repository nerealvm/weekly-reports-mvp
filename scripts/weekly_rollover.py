#!/usr/bin/env python3
"""Открыть новую неделю в листе «Активные».

Создаёт три недельные колонки, прячет прошлые недели в уже существующие
группы (не заводя новых) и переносит: результат прошлой недели -> статус
предыдущей недели, вехи прошлой недели -> вехи новой.

    python scripts/weekly_rollover.py            # показать план
    python scripts/weekly_rollover.py --apply    # выполнить
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from weekly_assistant.config.settings import load_settings
from weekly_assistant.integrations.google_sheets import GoogleSheetsAdapter
from weekly_assistant.services.active_sheet import (
    ACTIVE_SHEET_NAME,
    CURRENT_RESULT_GROUP,
    MILESTONE_GROUP,
    PREVIOUS_STATUS_GROUP,
    _row_at,
    _week_columns,
    column_letter,
    week_label_for_date,
)
from weekly_assistant.utils.weeks import current_week_bounds

TITLE_COL = 3
DATA_START_ROW = 3


class Abort(RuntimeError):
    pass


def retry(call, attempts=5, pause=3):
    """Повтор для чтений: соединение с Google рвётся на ровном месте.

    Записи через это не гоняем — вставка колонок не идемпотентна.
    """
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:
            if attempt == attempts:
                raise Abort(f"сеть недоступна после {attempts} попыток: {type(exc).__name__}") from exc
            print(f"  сеть подвела ({type(exc).__name__}), повтор {attempt}/{attempts - 1}")
            time.sleep(pause)


def read_header(adapter, sheet):
    header = retry(lambda: adapter.read_values(f"'{sheet}'!1:2"))
    return _row_at(header, 0), _row_at(header, 1)


def week_columns(adapter, sheet):
    first, second = read_header(adapter, sheet)
    return tuple(_week_columns(first, second, group) for group in (PREVIOUS_STATUS_GROUP, CURRENT_RESULT_GROUP, MILESTONE_GROUP))


def sheet_id_and_groups(adapter, sheet):
    meta = retry(lambda: adapter.spreadsheet_metadata("sheets(properties(title,sheetId),columnGroups)"))
    for item in meta.get("sheets", []):
        if item.get("properties", {}).get("title") == sheet:
            return item["properties"]["sheetId"], [g for g in item.get("columnGroups", []) if g.get("depth") == 1]
    raise Abort(f"лист «{sheet}» не найден")


def plan_labels(previous_status, current_result, milestone):
    _, end = current_week_bounds()
    new_label = week_label_for_date(end)
    already = current_result[-1].label == new_label
    # Статус предыдущей недели подписывается датой недели, результат которой в него копируется.
    status_label = current_result[-2].label if already else current_result[-1].label
    return status_label, new_label, already


def build_insert_requests(sheet_id, groups, status_label, new_label):
    previous_status, current_result, milestone = groups
    requests = []
    for columns, label in ((milestone, new_label), (current_result, new_label), (previous_status, status_label)):
        at = columns[-1].index  # 0-based индекс справа от последней колонки группы
        requests.append({"insertDimension": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": at, "endIndex": at + 1}, "inheritFromBefore": True}})
        requests.append({"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": at}, "rows": [{"values": [{"userEnteredValue": {"stringValue": label}}]}], "fields": "userEnteredValue"}})
    return requests


def build_group_requests(sheet_id, groups, existing):
    """Расширить каждую существующую группу на прошедшую неделю, не создавая новых."""
    deletes, targets = [], []
    for columns in groups:
        old = columns[:-1]
        start, end = old[0].index - 1, old[-1].index
        matches = [g for g in existing if g["range"]["startIndex"] < end and g["range"]["endIndex"] > start]
        if len(matches) != 1:
            raise Abort(f"в секции колонок {start + 1}..{end} ожидалась одна группа, найдено {len(matches)}")
        deletes.append({"deleteDimensionGroup": {"range": matches[0]["range"]}})
        targets.append({"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": start, "endIndex": end})
    return (
        deletes
        + [{"addDimensionGroup": {"range": r}} for r in targets]
        + [{"updateDimensionGroup": {"dimensionGroup": {"range": r, "depth": 1, "collapsed": True}, "fields": "collapsed"}} for r in targets],
        targets,
    )


def build_copy(adapter, sheet, source_col, target_col):
    values = retry(lambda: adapter.read_values(f"'{sheet}'!A1:DZ200"))

    def cell(row, col):
        return values[row][col - 1].strip() if row < len(values) and len(values[row]) >= col else ""

    rows = [r for r in range(DATA_START_ROW - 1, len(values)) if cell(r, TITLE_COL)]
    if any(cell(r, target_col) for r in rows):
        return None
    letter = column_letter(target_col)
    return [{"range": f"'{sheet}'!{letter}{r + 1}", "values": [[values[r][source_col - 1]]]} for r in rows if cell(r, source_col)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Открыть новую неделю в листе «Активные»")
    parser.add_argument("--apply", action="store_true", help="выполнить (по умолчанию только показать план)")
    parser.add_argument("--sheet", default=ACTIVE_SHEET_NAME)
    args = parser.parse_args()

    adapter = GoogleSheetsAdapter(load_settings())
    sheet = args.sheet
    try:
        groups = week_columns(adapter, sheet)
        sheet_id, existing = sheet_id_and_groups(adapter, sheet)
        status_label, new_label, already = plan_labels(*groups)
        previous_status, current_result, milestone = groups

        print(f"лист «{sheet}», новая неделя {new_label}")
        if already:
            print("  колонки недели уже созданы — продолжу с группировки и копирования")
        else:
            print(f"  статус предыдущей : новая колонка после {previous_status[-1].index} с меткой {status_label}")
            print(f"  результат         : новая колонка после {current_result[-1].index} с меткой {new_label}")
            print(f"  вехи              : новая колонка после {milestone[-1].index} с меткой {new_label}")
        print(f"  скрыть в существующие группы недели по {status_label} включительно")

        if not args.apply:
            print("\nсухой прогон. Для выполнения: --apply")
            return 0

        if not already:
            adapter.batch_update(build_insert_requests(sheet_id, groups, status_label, new_label))
            groups = week_columns(adapter, sheet)
        _, existing = sheet_id_and_groups(adapter, sheet)
        requests, targets = build_group_requests(sheet_id, groups, existing)
        adapter.batch_update(requests)
        print("группы: " + ", ".join(f"{t['startIndex'] + 1}..{t['endIndex']}" for t in targets))

        previous_status, current_result, milestone = groups
        for name, source, target in (
            ("статус предыдущей", current_result[-2].index, previous_status[-1].index),
            ("вехи", milestone[-2].index, milestone[-1].index),
        ):
            data = build_copy(adapter, sheet, source, target)
            if data is None:
                print(f"{name}: {column_letter(target)} уже заполнена, пропускаю")
                continue
            written = adapter.batch_update_values(data, value_input_option="RAW").get("totalUpdatedCells", 0) if data else 0
            print(f"{name}: {column_letter(source)} -> {column_letter(target)}, ячеек {written}")

        print(f"\nготово. Заполнить вручную: «Куда мы докатились» {new_label}")
        return 0
    except Abort as exc:
        print(f"СТОП: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
