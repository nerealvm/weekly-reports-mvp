import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from weekly_assistant.adapters.csv_adapter import HEADERS
from weekly_assistant.domain.enums import Lifecycle, MovementType, ReviewStatus, YesNo
from weekly_assistant.integrations.google_sheets import GoogleSheetsAdapter


ACTIVE_SHEET_NAME = "Активные"
ACTIVE_GID = "0"
ACTIVE_SOURCE_FORMAT = "active_legacy"

PREVIOUS_STATUS_GROUP = "статус предыдущей недели"
CURRENT_RESULT_GROUP = "куда мы докатились"
MILESTONE_GROUP = "когда докатимся"
SERVICE_FIELD_HEADERS = (
    ("movement_type", HEADERS["movement_type"]),
    ("needs_sync", HEADERS["needs_sync"]),
    ("sync_reason", HEADERS["sync_reason"]),
)
# Topic ID is a visible anchor column kept at the front of the sheet (not a
# hidden service column). The collector reads/writes it by header; write-back
# maps rows to physical sheet rows by it so the mapping can't drift.
TOPIC_ID_HEADER = HEADERS["topic_id"]


@dataclass(frozen=True)
class WeekColumn:
    label: str
    index: int


@dataclass(frozen=True)
class ActiveSheetColumns:
    week_label: str
    topic_col: int
    date_created_col: int
    previous_status_col: int | None
    previous_result_source_col: int | None
    current_result_col: int | None
    milestone_col: int | None
    milestone_source_col: int | None
    ball_col: int
    question_col: int
    movement_col: int | None = None
    needs_sync_col: int | None = None
    sync_reason_col: int | None = None
    topic_id_col: int | None = None

    def to_dict(self) -> dict:
        return {
            "week_label": self.week_label,
            "topic_col": self.topic_col,
            "date_created_col": self.date_created_col,
            "previous_status_col": self.previous_status_col,
            "previous_result_source_col": self.previous_result_source_col,
            "current_result_col": self.current_result_col,
            "milestone_col": self.milestone_col,
            "milestone_source_col": self.milestone_source_col,
            "ball_col": self.ball_col,
            "question_col": self.question_col,
            "movement_col": self.movement_col,
            "needs_sync_col": self.needs_sync_col,
            "sync_reason_col": self.sync_reason_col,
            "topic_id_col": self.topic_id_col,
        }


def week_label_for_date(value: date) -> str:
    return f"{value.day}.{value.month:02d}"


def is_active_sheet_csv(path: str | Path) -> bool:
    rows = _read_csv_matrix(path, limit=2)
    if not rows:
        return False
    first = rows[0]
    normalized = " ".join(_normalize(cell) for cell in first)
    return (
        "тема" in normalized
        and PREVIOUS_STATUS_GROUP in normalized
        and CURRENT_RESULT_GROUP in normalized
        and MILESTONE_GROUP in normalized
    )


def build_active_session_rows_from_csv(path: str | Path, *, week_label: str) -> tuple[list[dict], list[dict], dict]:
    rows = _read_csv_matrix(path)
    if len(rows) < 2:
        raise ValueError("Active sheet CSV must contain two header rows.")

    header_values = [rows[0], rows[1]]
    columns = resolve_active_sheet_columns(header_values, week_label=week_label)
    session_rows: list[dict] = []
    raw_rows: list[dict] = []

    # first pass: collect topic rows so persisted Topic IDs anchor the mapping
    topic_rows: list[tuple[int, list[str], str]] = []
    current_section = ""
    for row_number, row in enumerate(rows[2:], start=3):
        section_cell = _cell(row, 1)
        topic_title = _cell(row, columns.topic_col)
        if not topic_title:
            if section_cell:
                current_section = section_cell
            continue
        topic_rows.append((row_number, row, current_section or "Проекты"))
    next_topic_number = _next_topic_number(topic_rows, columns)

    for row_number, row, section in topic_rows:
        topic_title = _cell(row, columns.topic_col)
        existing_id = _cell(row, columns.topic_id_col)
        if existing_id:
            topic_id = existing_id
            topic_id_persisted = True
        else:
            topic_id = f"T-{next_topic_number:03d}"
            next_topic_number += 1
            topic_id_persisted = False
        previous_result = _first_non_empty(
            _cell(row, columns.previous_status_col),
            _cell(row, columns.previous_result_source_col),
        )
        final_result = _cell(row, columns.current_result_col)
        milestone = _first_non_empty(
            _cell(row, columns.milestone_col),
            _cell(row, columns.milestone_source_col),
        )
        milestones = _parse_milestones(milestone)
        milestone_date = milestones[0]["date"] if milestones else ""
        open_question = _cell(row, columns.question_col)
        movement_type = _normalize_enum_value(_cell(row, columns.movement_col), MovementType, _movement_type_for_result(final_result))
        needs_sync = _normalize_enum_value(
            _cell(row, columns.needs_sync_col),
            YesNo,
            YesNo.YES.value if open_question else YesNo.NO.value,
        )
        sync_reason = _cell(row, columns.sync_reason_col) or ("Есть открытый вопрос к Евгению." if open_question else "")
        lifecycle = _lifecycle_for_section(section)
        row_model = {
            "row_number": row_number,
            "topic_id": topic_id,
            "topic_id_persisted": topic_id_persisted,
            "topic_title": topic_title,
            "section": section,
            "date_created": _cell(row, columns.date_created_col),
            "lifecycle": lifecycle,
            "focus": YesNo.NO.value,
            "previous_week_result": previous_result,
            "raw_fact": "",
            "current_week_facts": "",
            "ai_draft_result": "",
            "final_result": final_result,
            "next_milestone": milestone,
            "next_milestone_date": milestone_date,
            "ball_side": _cell(row, columns.ball_col),
            "open_question_to_evgeny": open_question,
            "manual_current_week_facts": False,
            "manual_open_question_to_evgeny": False,
            "milestones": milestones,
            "milestones_text": _format_milestones_text(milestones),
            "movement_type": movement_type,
            "needs_sync": needs_sync,
            "sync_reason": sync_reason,
            "source_links": "",
            "review_status": ReviewStatus.DRAFT.value,
            "status": "has_facts" if final_result else "empty",
            "changed": False,
            "changed_fields": [],
            "is_new": False,
            "hints": [],
        }
        session_rows.append(row_model)
        raw_rows.append(row_to_normalized_raw(row_model))

    metadata = {
        "source_format": ACTIVE_SOURCE_FORMAT,
        "active_sheet": {
            "sheet_name": ACTIVE_SHEET_NAME,
            "week_label": week_label,
            "columns": columns.to_dict(),
        },
    }
    return session_rows, raw_rows, metadata


def _next_topic_number(topic_rows: list[tuple[int, list[str], str]], columns: "ActiveSheetColumns") -> int:
    max_number = 0
    for _row_number, row, _section in topic_rows:
        match = re.match(r"[Tt]-?(\d+)", _cell(row, columns.topic_id_col))
        if match:
            max_number = max(max_number, int(match.group(1)))
    return max_number + 1


def row_to_normalized_raw(row: dict) -> dict:
    raw = {header: "" for header in HEADERS.values()}
    raw["_row_number"] = row["row_number"]
    for key, header in HEADERS.items():
        raw[header] = _normalized_row_value(row, key)
    return raw


def resolve_active_sheet_columns(header_values: list[list[str]], *, week_label: str) -> ActiveSheetColumns:
    first = _row_at(header_values, 0)
    second = _row_at(header_values, 1)
    current_columns = _week_columns(first, second, CURRENT_RESULT_GROUP)
    milestone_columns = _week_columns(first, second, MILESTONE_GROUP)
    previous_columns = _week_columns(first, second, PREVIOUS_STATUS_GROUP)
    return ActiveSheetColumns(
        week_label=week_label,
        topic_col=_find_header_column(first, "тема"),
        date_created_col=_find_header_column(first, "дата постановки"),
        previous_status_col=_find_week_column(previous_columns, week_label),
        previous_result_source_col=_latest_previous_or_last(current_columns, week_label),
        current_result_col=_find_week_column(current_columns, week_label),
        milestone_col=_find_week_column(milestone_columns, week_label),
        milestone_source_col=_latest_previous_or_last(milestone_columns, week_label),
        ball_col=_find_header_column(first, "на чьей стороне мяч"),
        question_col=_find_header_column(first, "открытые вопросы"),
        movement_col=_find_optional_header_column(first, HEADERS["movement_type"]),
        needs_sync_col=_find_optional_header_column(first, HEADERS["needs_sync"]),
        sync_reason_col=_find_optional_header_column(first, HEADERS["sync_reason"]),
        topic_id_col=_find_optional_header_column(first, HEADERS["topic_id"]),
    )


def ensure_active_week_columns(
    adapter: GoogleSheetsAdapter,
    *,
    target_sheet: str,
    week_label: str,
) -> tuple[ActiveSheetColumns, tuple[str, ...]]:
    header_values = adapter.read_values(f"'{target_sheet}'!1:2")
    requests, created = build_active_week_column_requests(adapter, target_sheet, header_values, week_label)
    if requests:
        adapter.batch_update(requests)
        header_values = adapter.read_values(f"'{target_sheet}'!1:2")
    return resolve_active_sheet_columns(header_values, week_label=week_label), tuple(created)


def build_active_week_column_requests(
    adapter: GoogleSheetsAdapter,
    target_sheet: str,
    header_values: list[list[str]],
    week_label: str,
) -> tuple[list[dict], list[str]]:
    sheet_id = _sheet_id(adapter, target_sheet)
    first = _row_at(header_values, 0)
    second = _row_at(header_values, 1)
    service_requests, service_created = _service_column_requests(sheet_id, first, second)
    insertions = []
    for group_query, label in (
        (PREVIOUS_STATUS_GROUP, "previous_status"),
        (CURRENT_RESULT_GROUP, "current_result"),
        (MILESTONE_GROUP, "milestone"),
    ):
        insertion_index = _missing_week_insertion_index(first, second, group_query, week_label)
        if insertion_index is not None:
            insertions.append((insertion_index, label))

    requests = list(service_requests)
    created = []
    for insertion_index, label in sorted(insertions, reverse=True):
        start_index = insertion_index - 1
        requests.extend(
            [
                {
                    "insertDimension": {
                        "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": start_index, "endIndex": start_index + 1},
                        "inheritFromBefore": True,
                    }
                },
                {
                    "updateCells": {
                        "start": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": start_index},
                        "rows": [{"values": [{"userEnteredValue": {"stringValue": week_label}}]}],
                        "fields": "userEnteredValue",
                    }
                },
            ]
        )
        created.append(label)
    return requests, list(reversed(created)) + service_created


def active_row_updates(row: dict, columns: ActiveSheetColumns) -> list[tuple[int, str, str]]:
    updates: list[tuple[int, str, str]] = []
    changed_fields = set(row.get("changed_fields", []))
    if columns.previous_status_col:
        updates.append((columns.previous_status_col, row.get("previous_week_result", ""), "previous_week_result"))
    if "topic_title" in changed_fields:
        updates.append((columns.topic_col, row.get("topic_title", ""), "topic_title"))
    if "date_created" in changed_fields:
        updates.append((columns.date_created_col, row.get("date_created", ""), "date_created"))

    write_all_outputs = bool(row.get("changed")) and not changed_fields and row.get("status") == "chatgpt_imported"
    if write_all_outputs or changed_fields & {"current_week_facts", "ai_draft_result", "final_result", "movement_type", "review_status"}:
        if columns.current_result_col:
            updates.append((columns.current_result_col, row.get("final_result", ""), "final_result"))
    if write_all_outputs or changed_fields & {"next_milestone", "next_milestone_date", "milestones_text"}:
        if columns.milestone_col:
            prefer_next = "milestones_text" not in changed_fields and bool(changed_fields & {"next_milestone", "next_milestone_date"})
            updates.append((columns.milestone_col, _active_milestone_value(row, prefer_next=prefer_next), "next_milestone"))
    if write_all_outputs or "ball_side" in changed_fields:
        updates.append((columns.ball_col, row.get("ball_side", ""), "ball_side"))
    if write_all_outputs or "open_question_to_evgeny" in changed_fields:
        updates.append((columns.question_col, row.get("open_question_to_evgeny", ""), "open_question_to_evgeny"))
    if columns.movement_col:
        updates.append((columns.movement_col, row.get("movement_type", MovementType.UNCLEAR.value), "movement_type"))
    if columns.needs_sync_col:
        updates.append((columns.needs_sync_col, row.get("needs_sync", YesNo.NO.value), "needs_sync"))
    if columns.sync_reason_col:
        updates.append((columns.sync_reason_col, row.get("sync_reason", ""), "sync_reason"))
    if columns.topic_id_col and not row.get("topic_id_persisted", False):
        updates.append((columns.topic_id_col, row.get("topic_id", ""), "topic_id"))
    return _dedupe_updates(updates)


def active_append_row_values(row: dict, columns: ActiveSheetColumns) -> list[str]:
    width = max(
        columns.topic_col,
        columns.date_created_col,
        columns.previous_status_col or 0,
        columns.current_result_col or 0,
        columns.milestone_col or 0,
        columns.ball_col,
        columns.question_col,
        columns.movement_col or 0,
        columns.needs_sync_col or 0,
        columns.sync_reason_col or 0,
        columns.topic_id_col or 0,
    )
    values = [""] * width
    _set(values, columns.topic_col, row.get("topic_title", ""))
    _set(values, columns.date_created_col, row.get("date_created", ""))
    if columns.previous_status_col:
        _set(values, columns.previous_status_col, row.get("previous_week_result", ""))
    if columns.current_result_col:
        _set(values, columns.current_result_col, row.get("final_result", ""))
    if columns.milestone_col:
        _set(values, columns.milestone_col, _active_milestone_value(row))
    _set(values, columns.ball_col, row.get("ball_side", ""))
    _set(values, columns.question_col, row.get("open_question_to_evgeny", ""))
    if columns.movement_col:
        _set(values, columns.movement_col, row.get("movement_type", MovementType.UNCLEAR.value))
    if columns.needs_sync_col:
        _set(values, columns.needs_sync_col, row.get("needs_sync", YesNo.NO.value))
    if columns.sync_reason_col:
        _set(values, columns.sync_reason_col, row.get("sync_reason", ""))
    if columns.topic_id_col:
        _set(values, columns.topic_id_col, row.get("topic_id", ""))
    return values


def column_letter(index: int | None) -> str:
    if not index or index < 1:
        return ""
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _read_csv_matrix(path: str | Path, limit: int | None = None) -> list[list[str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        rows = []
        for row in reader:
            rows.append([str(cell).strip() for cell in row])
            if limit and len(rows) >= limit:
                break
        return rows


def _week_columns(first_row: list[str], second_row: list[str], group_query: str) -> list[WeekColumn]:
    group_start = _find_header_column(first_row, group_query)
    next_group_start = _next_non_empty_index(first_row, group_start + 1) or max(len(first_row), len(second_row)) + 1
    columns = []
    for index in range(group_start + 1, next_group_start):
        label = _cell(second_row, index)
        if label:
            columns.append(WeekColumn(label=label, index=index))
    return columns


def _find_week_column(columns: list[WeekColumn], week_label: str) -> int | None:
    for column in columns:
        if column.label == week_label:
            return column.index
    return None


def _latest_previous_or_last(columns: list[WeekColumn], week_label: str) -> int | None:
    if not columns:
        return None
    for position, column in enumerate(columns):
        if column.label == week_label:
            previous = columns[:position]
            return previous[-1].index if previous else None
    return columns[-1].index


def _missing_week_insertion_index(first_row: list[str], second_row: list[str], group_query: str, week_label: str) -> int | None:
    group_start = _find_header_column(first_row, group_query)
    next_group_start = _next_non_empty_index(first_row, group_start + 1)
    group_end = (next_group_start - 1) if next_group_start else max(len(first_row), len(second_row))
    for index in range(group_start + 1, group_end + 1):
        if _cell(second_row, index) == week_label:
            return None
    return next_group_start or (max(len(first_row), len(second_row)) + 1)


def _sheet_id(adapter: GoogleSheetsAdapter, title: str) -> int:
    for properties in adapter.sheet_properties():
        if properties.get("title") == title:
            return int(properties["sheetId"])
    raise ValueError(f"Sheet not found: {title}")


def _find_header_column(row: list[str], query: str) -> int:
    normalized_query = _normalize(query)
    for index, value in enumerate(row, start=1):
        if normalized_query in _normalize(value):
            return index
    raise ValueError(f"Could not find header column containing: {query}")


def _find_optional_header_column(row: list[str], query: str) -> int | None:
    normalized_query = _normalize(query)
    for index, value in enumerate(row, start=1):
        if _normalize(value) == normalized_query:
            return index
    return None


def _service_column_requests(sheet_id: int, first_row: list[str], second_row: list[str]) -> tuple[list[dict], list[str]]:
    requests: list[dict] = []
    created: list[str] = []
    missing = [(field, header) for field, header in SERVICE_FIELD_HEADERS if not _find_optional_header_column(first_row, header)]

    if missing:
        start_index = max(len(first_row), len(second_row))
        end_index = start_index + len(missing)
        requests.append(
            {
                "insertDimension": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": start_index, "endIndex": end_index},
                    "inheritFromBefore": True,
                }
            }
        )
        requests.append(
            {
                "updateCells": {
                    "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": start_index},
                    "rows": [
                        {
                            "values": [
                                {"userEnteredValue": {"stringValue": header}}
                                for _field, header in missing
                            ]
                        }
                    ],
                    "fields": "userEnteredValue",
                }
            }
        )
        requests.append(_hide_columns_request(sheet_id, start_index, end_index))
        created.extend(field for field, _header in missing)

    for _field, header in SERVICE_FIELD_HEADERS:
        column = _find_optional_header_column(first_row, header)
        if column:
            requests.append(_hide_columns_request(sheet_id, column - 1, column))

    return requests, created


def _hide_columns_request(sheet_id: int, start_index: int, end_index: int) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": start_index, "endIndex": end_index},
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }
    }


def _next_non_empty_index(row: list[str], start_index: int) -> int | None:
    for index in range(start_index, len(row) + 1):
        if _cell(row, index):
            return index
    return None


def _row_at(values: list[list[str]], index: int) -> list[str]:
    return values[index] if index < len(values) else []


def _cell(row: list[str], index: int | None) -> str:
    if not index or index < 1 or index > len(row):
        return ""
    return str(row[index - 1]).strip()


def _set(values: list[str], index: int, value: str) -> None:
    while len(values) < index:
        values.append("")
    values[index - 1] = str(value or "")


def _first_non_empty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def _lifecycle_for_section(section: str) -> str:
    normalized = _normalize(section)
    if "архив" in normalized:
        return Lifecycle.ARCHIVED.value
    if "закры" in normalized:
        return Lifecycle.CLOSED.value
    if "пауз" in normalized or "холд" in normalized:
        return Lifecycle.PAUSED.value
    return Lifecycle.ACTIVE.value


def _movement_type_for_result(value: str) -> str:
    normalized = _normalize(value)
    if not normalized:
        return MovementType.UNCLEAR.value
    if "без нового движения" in normalized or "без движения" in normalized:
        return MovementType.NO_MOVEMENT.value
    return MovementType.REAL_RESULT.value


def _parse_milestones(value: str) -> list[dict]:
    milestones = []
    for line in (value or "").replace(";", "\n").splitlines():
        raw = re.sub(r"^\s*[-*]\s*", "", line).strip()
        if not raw:
            continue
        match = re.match(r"^(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2}|TBD)\s+(.+)$", raw, flags=re.IGNORECASE)
        if match:
            milestones.append({"date": match.group(1).strip(), "text": match.group(2).strip()})
        else:
            milestones.append({"date": "", "text": raw})
    return milestones


def _format_milestones_text(milestones: list[dict]) -> str:
    return "\n".join(_format_milestone(item) for item in milestones if _format_milestone(item))


def _format_milestone(item: dict) -> str:
    text = str(item.get("text", "")).strip()
    date_value = str(item.get("date", "")).strip()
    if text and date_value:
        return f"{date_value} {text}"
    return text or date_value


def _active_milestone_value(row: dict, *, prefer_next: bool = False) -> str:
    if prefer_next:
        text = str(row.get("next_milestone", "") or "").strip()
        date_value = str(row.get("next_milestone_date", "") or "").strip()
        if text and date_value and not _starts_with_date(text):
            return f"{date_value} {text}"
        return text
    return row.get("milestones_text", "").replace("\n", "; ") or row.get("next_milestone", "")


def _starts_with_date(value: str) -> bool:
    return bool(
        re.match(
            r"^\s*(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2}|TBD)\b",
            value,
            flags=re.IGNORECASE,
        )
    )


def _normalized_row_value(row: dict, field: str) -> str:
    value = row.get(field, "")
    if field == "lifecycle" and value not in {item.value for item in Lifecycle}:
        return Lifecycle.ACTIVE.value
    if field == "focus" and value not in {item.value for item in YesNo}:
        return YesNo.NO.value
    if field == "movement_type" and value not in {item.value for item in MovementType}:
        return MovementType.UNCLEAR.value
    if field == "needs_sync" and value not in {item.value for item in YesNo}:
        return YesNo.NO.value
    if field == "review_status" and value not in {item.value for item in ReviewStatus}:
        return ReviewStatus.DRAFT.value
    return str(value or "")


def _normalize_enum_value(value: str, enum_type, default: str) -> str:
    try:
        return enum_type((value or default).strip()).value
    except ValueError:
        return default


def _dedupe_updates(updates: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
    by_column: dict[int, tuple[int, str, str]] = {}
    for update in updates:
        by_column[update[0]] = update
    return list(by_column.values())


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()
