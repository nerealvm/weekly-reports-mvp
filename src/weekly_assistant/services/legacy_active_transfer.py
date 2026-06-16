import re
from dataclasses import dataclass

from weekly_assistant.adapters.csv_adapter import HEADERS
from weekly_assistant.integrations.google_sheets import GoogleSheetsAdapter


DEFAULT_TARGET_SHEET = "Активные"
LEGACY_ROW_HEADER = "Legacy row"


@dataclass(frozen=True)
class LegacyTransferColumns:
    status_col: int
    milestone_col: int
    ball_col: int
    question_col: int | None
    status_label: str
    milestone_label: str


@dataclass(frozen=True)
class LegacyTransferItem:
    topic_id: str
    topic_title: str
    legacy_row: int
    updates: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class LegacyTransferResult:
    source_sheet: str
    target_sheet: str
    dry_run: bool
    columns: LegacyTransferColumns
    items: tuple[LegacyTransferItem, ...]
    skipped: tuple[str, ...]
    created_week_columns: tuple[str, ...] = ()
    created_legacy_rows: tuple[tuple[str, str, int], ...] = ()


def transfer_weekly_to_active(
    adapter: GoogleSheetsAdapter,
    *,
    source_sheet: str,
    target_sheet: str = DEFAULT_TARGET_SHEET,
    week_label: str = "",
    status_col: str = "",
    milestone_col: str = "",
    ball_col: str = "",
    question_col: str = "",
    include_open_questions: bool = False,
    ensure_week_columns: bool = False,
    create_missing_legacy_rows: bool = False,
    apply: bool = False,
) -> LegacyTransferResult:
    source_values = adapter.read_values(f"'{source_sheet}'!A:T")
    target_header_values = adapter.read_values(f"'{target_sheet}'!1:2")
    created_week_columns: tuple[str, ...] = ()
    if ensure_week_columns and week_label:
        if apply and not (status_col or milestone_col):
            columns, created_week_columns = ensure_legacy_week_columns(adapter, target_sheet=target_sheet, week_label=week_label)
            target_header_values = adapter.read_values(f"'{target_sheet}'!1:2")
        elif not apply:
            target_header_values, created_week_columns = preview_legacy_week_columns(target_header_values, week_label)
            columns = resolve_legacy_columns(
                target_header_values,
                week_label=week_label,
                status_col=status_col,
                milestone_col=milestone_col,
                ball_col=ball_col,
                question_col=question_col,
                include_open_questions=include_open_questions,
            )
        else:
            columns = resolve_legacy_columns(
                target_header_values,
                week_label=week_label,
                status_col=status_col,
                milestone_col=milestone_col,
                ball_col=ball_col,
                question_col=question_col,
                include_open_questions=include_open_questions,
            )
    else:
        columns = resolve_legacy_columns(
            target_header_values,
            week_label=week_label,
            status_col=status_col,
            milestone_col=milestone_col,
            ball_col=ball_col,
            question_col=question_col,
            include_open_questions=include_open_questions,
        )
    created_legacy_rows: tuple[tuple[str, str, int], ...] = ()
    if create_missing_legacy_rows and apply:
        source_values, created_legacy_rows = ensure_legacy_rows(
            adapter,
            source_sheet=source_sheet,
            target_sheet=target_sheet,
            source_values=source_values,
            target_header_values=target_header_values,
        )
    items, skipped = build_legacy_transfer_items(source_values, columns, include_open_questions=include_open_questions)
    if apply:
        for item in items:
            for col_index, value in item.updates:
                adapter.update_values(f"'{target_sheet}'!{column_letter(col_index)}{item.legacy_row}", [[value]])
    return LegacyTransferResult(
        source_sheet=source_sheet,
        target_sheet=target_sheet,
        dry_run=not apply,
        columns=columns,
        items=tuple(items),
        skipped=tuple(skipped),
        created_week_columns=created_week_columns,
        created_legacy_rows=created_legacy_rows,
    )


def ensure_legacy_week_columns(
    adapter: GoogleSheetsAdapter,
    *,
    target_sheet: str,
    week_label: str,
) -> tuple[LegacyTransferColumns, tuple[str, ...]]:
    header_values = adapter.read_values(f"'{target_sheet}'!1:2")
    requests, created = build_legacy_week_column_requests(adapter, target_sheet, header_values, week_label)
    if requests:
        adapter.batch_update(requests)
        header_values = adapter.read_values(f"'{target_sheet}'!1:2")
    return resolve_legacy_columns(header_values, week_label=week_label), tuple(created)


def preview_legacy_week_columns(header_values: list[list[str]], week_label: str) -> tuple[list[list[str]], tuple[str, ...]]:
    first = list(_row_at(header_values, 0))
    second = list(_row_at(header_values, 1))
    created: list[str] = []
    for group_query, label in (("когда докатимся", "milestone"), ("куда мы докатились", "status")):
        insertion_index = _missing_week_insertion_index(first, second, group_query, week_label)
        if insertion_index is None:
            continue
        _insert_cell(first, insertion_index, "")
        _insert_cell(second, insertion_index, week_label)
        created.append(label)
    return [first, second], tuple(reversed(created))


def build_legacy_week_column_requests(
    adapter: GoogleSheetsAdapter,
    target_sheet: str,
    header_values: list[list[str]],
    week_label: str,
) -> tuple[list[dict], list[str]]:
    sheet_id = _sheet_id(adapter, target_sheet)
    first = _row_at(header_values, 0)
    second = _row_at(header_values, 1)
    insertions = []
    for group_query, label in (("куда мы докатились", "status"), ("когда докатимся", "milestone")):
        insertion_index = _missing_week_insertion_index(first, second, group_query, week_label)
        if insertion_index is not None:
            insertions.append((insertion_index, label))
    requests = []
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
    return requests, list(reversed(created))


def ensure_legacy_rows(
    adapter: GoogleSheetsAdapter,
    *,
    source_sheet: str,
    target_sheet: str,
    source_values: list[list[str]],
    target_header_values: list[list[str]],
) -> tuple[list[list[str]], tuple[tuple[str, str, int], ...]]:
    if not source_values:
        return source_values, ()
    headers = source_values[0]
    header_index = {value: index for index, value in enumerate(headers)}
    legacy_index = header_index.get(LEGACY_ROW_HEADER)
    if legacy_index is None:
        return source_values, ()
    source_rows = [list(row) for row in source_values]
    rows_to_append = []
    source_row_numbers = []
    created = []
    for source_row_number, row in enumerate(source_rows[1:], start=2):
        if _source_value(row, header_index, LEGACY_ROW_HEADER):
            continue
        if _source_value(row, header_index, HEADERS["lifecycle"]).casefold() != "active":
            continue
        topic_title = _source_value(row, header_index, HEADERS["topic_title"])
        if not topic_title:
            continue
        rows_to_append.append(_legacy_row_values(row, header_index, target_header_values))
        source_row_numbers.append(source_row_number)
    if not rows_to_append:
        return source_rows, ()
    result = adapter.append_values(f"'{target_sheet}'!A:ZZ", rows_to_append)
    start_row = _parse_updated_range_start_row(result.get("updates", {}).get("updatedRange", ""))
    if not start_row:
        raise RuntimeError("Could not determine appended legacy row number.")
    for offset, source_row_number in enumerate(source_row_numbers):
        legacy_row = start_row + offset
        source_row = source_rows[source_row_number - 1]
        while len(source_row) <= legacy_index:
            source_row.append("")
        source_row[legacy_index] = str(legacy_row)
        topic_id = _source_value(source_row, header_index, HEADERS["topic_id"])
        topic_title = _source_value(source_row, header_index, HEADERS["topic_title"])
        adapter.update_values(f"'{source_sheet}'!{column_letter(legacy_index + 1)}{source_row_number}", [[str(legacy_row)]])
        created.append((topic_id, topic_title, legacy_row))
    return source_rows, tuple(created)


def resolve_legacy_columns(
    header_values: list[list[str]],
    *,
    week_label: str = "",
    status_col: str = "",
    milestone_col: str = "",
    ball_col: str = "",
    question_col: str = "",
    include_open_questions: bool = False,
) -> LegacyTransferColumns:
    first_row = _row_at(header_values, 0)
    second_row = _row_at(header_values, 1)
    status_index = column_index(status_col) if status_col else _find_week_column(first_row, second_row, "куда мы докатились", week_label)
    milestone_index = column_index(milestone_col) if milestone_col else _find_week_column(first_row, second_row, "когда докатимся", week_label)
    ball_index = column_index(ball_col) if ball_col else _find_header_column(first_row, "на чьей стороне мяч")
    question_index = None
    if include_open_questions:
        question_index = column_index(question_col) if question_col else _find_header_column(first_row, "открытые вопросы")
    return LegacyTransferColumns(
        status_col=status_index,
        milestone_col=milestone_index,
        ball_col=ball_index,
        question_col=question_index,
        status_label=_cell(second_row, status_index) or column_letter(status_index),
        milestone_label=_cell(second_row, milestone_index) or column_letter(milestone_index),
    )


def build_legacy_transfer_items(
    source_values: list[list[str]],
    columns: LegacyTransferColumns,
    *,
    include_open_questions: bool = False,
) -> tuple[list[LegacyTransferItem], list[str]]:
    if not source_values:
        return [], ["source sheet is empty"]
    headers = source_values[0]
    header_index = {value: index for index, value in enumerate(headers)}
    required = [
        HEADERS["topic_title"],
        HEADERS["lifecycle"],
        HEADERS["final_result"],
        HEADERS["next_milestone"],
        HEADERS["next_milestone_date"],
        HEADERS["ball_side"],
        LEGACY_ROW_HEADER,
    ]
    missing = [header for header in required if header not in header_index]
    if missing:
        return [], [f"source sheet missing columns: {', '.join(missing)}"]
    items: list[LegacyTransferItem] = []
    skipped: list[str] = []
    for row_number, row in enumerate(source_values[1:], start=2):
        topic_title = _sheet_value(row, header_index, HEADERS["topic_title"])
        topic_id = _sheet_value(row, header_index, HEADERS["topic_id"])
        if not topic_title:
            continue
        lifecycle = _sheet_value(row, header_index, HEADERS["lifecycle"]).casefold()
        if lifecycle != "active":
            skipped.append(f"source row {row_number} {topic_title}: lifecycle={lifecycle or '-'}")
            continue
        legacy_row_raw = _sheet_value(row, header_index, LEGACY_ROW_HEADER)
        if not legacy_row_raw:
            skipped.append(f"source row {row_number} {topic_title}: empty Legacy row")
            continue
        try:
            legacy_row = int(float(legacy_row_raw))
        except ValueError:
            skipped.append(f"source row {row_number} {topic_title}: invalid Legacy row={legacy_row_raw}")
            continue
        final_result = _sheet_value(row, header_index, HEADERS["final_result"])
        milestone = _format_legacy_milestone(
            _sheet_value(row, header_index, HEADERS["next_milestone"]),
            _sheet_value(row, header_index, HEADERS["next_milestone_date"]),
        )
        ball_side = _sheet_value(row, header_index, HEADERS["ball_side"])
        updates = []
        if final_result:
            updates.append((columns.status_col, final_result))
        if milestone:
            updates.append((columns.milestone_col, milestone))
        if ball_side:
            updates.append((columns.ball_col, ball_side))
        if include_open_questions and columns.question_col:
            question = _sheet_value(row, header_index, HEADERS["open_question_to_evgeny"])
            if question:
                updates.append((columns.question_col, question))
        if not updates:
            skipped.append(f"source row {row_number} {topic_title}: no values to transfer")
            continue
        items.append(
            LegacyTransferItem(
                topic_id=topic_id,
                topic_title=topic_title,
                legacy_row=legacy_row,
                updates=tuple(updates),
            )
        )
    return items, skipped


def format_legacy_transfer_result(result: LegacyTransferResult) -> str:
    mode = "DRY RUN" if result.dry_run else "APPLIED"
    lines = [
        f"{mode}: {result.source_sheet} -> {result.target_sheet}",
        (
            "Columns: "
            f"status={column_letter(result.columns.status_col)} ({result.columns.status_label}), "
            f"milestone={column_letter(result.columns.milestone_col)} ({result.columns.milestone_label}), "
            f"ball={column_letter(result.columns.ball_col)}"
            + (f", question={column_letter(result.columns.question_col)}" if result.columns.question_col else "")
        ),
        f"Rows to update: {len(result.items)}",
    ]
    for item in result.items:
        updates = ", ".join(f"{column_letter(col)}{item.legacy_row}={_preview(value)}" for col, value in item.updates)
        lines.append(f"- {item.topic_id or '-'} {item.topic_title}: {updates}")
    if result.created_week_columns:
        lines.append(f"Created week columns: {', '.join(result.created_week_columns)}")
    if result.created_legacy_rows:
        lines.append("Created legacy rows:")
        lines.extend(f"- {topic_id or '-'} {topic_title}: row {legacy_row}" for topic_id, topic_title, legacy_row in result.created_legacy_rows)
    if result.skipped:
        lines.append("Skipped:")
        lines.extend(f"- {item}" for item in result.skipped)
    if result.dry_run:
        lines.append("Nothing was written. Re-run with --apply to update Google Sheet.")
    return "\n".join(lines)


def column_index(value: str) -> int:
    text = value.strip().upper()
    if not re.fullmatch(r"[A-Z]+", text):
        raise ValueError(f"Invalid column letter: {value}")
    result = 0
    for char in text:
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def column_letter(index: int | None) -> str:
    if not index or index < 1:
        return ""
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _missing_week_insertion_index(first_row: list[str], second_row: list[str], group_query: str, week_label: str) -> int | None:
    group_start = _find_header_column(first_row, group_query)
    next_group_start = _next_non_empty_index(first_row, group_start + 1)
    group_end = (next_group_start - 1) if next_group_start else max(len(first_row), len(second_row))
    for index in range(group_start + 1, group_end + 1):
        if _cell(second_row, index).strip() == week_label:
            return None
    return next_group_start or (max(len(first_row), len(second_row)) + 1)


def _insert_cell(row: list[str], index: int, value: str) -> None:
    while len(row) < index - 1:
        row.append("")
    row.insert(index - 1, value)


def _sheet_id(adapter: GoogleSheetsAdapter, title: str) -> int:
    for properties in adapter.sheet_properties():
        if properties.get("title") == title:
            return int(properties["sheetId"])
    raise ValueError(f"Sheet not found: {title}")


def _legacy_row_values(row: list[str], header_index: dict[str, int], target_header_values: list[list[str]]) -> list[str]:
    first = _row_at(target_header_values, 0)
    second = _row_at(target_header_values, 1)
    width = max(len(first), len(second), 4)
    values = [""] * width
    _set_if_found(values, first, "тема", _source_value(row, header_index, HEADERS["topic_title"]))
    _set_if_found(values, first, "дата постановки", _source_value(row, header_index, HEADERS["date_created"]))
    _set_if_found(values, first, "статус предыдущей недели", _source_value(row, header_index, HEADERS["previous_week_result"]))
    _set_if_found(values, first, "на чьей стороне мяч", _source_value(row, header_index, HEADERS["ball_side"]))
    _set_if_found(values, first, "открытые вопросы", _source_value(row, header_index, HEADERS["open_question_to_evgeny"]))
    return values


def _set_if_found(values: list[str], header_row: list[str], query: str, value: str) -> None:
    if not value:
        return
    try:
        index = _find_header_column(header_row, query)
    except ValueError:
        return
    while len(values) < index:
        values.append("")
    values[index - 1] = value


def _source_value(row: list[str], header_index: dict[str, int], header: str) -> str:
    index = header_index.get(header)
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def _parse_updated_range_start_row(value: str) -> int | None:
    match = re.search(r"![A-Z]+(\d+):", str(value or ""))
    return int(match.group(1)) if match else None


def _find_week_column(first_row: list[str], second_row: list[str], group_query: str, week_label: str) -> int:
    group_start = _find_header_column(first_row, group_query)
    next_group_start = _next_non_empty_index(first_row, group_start + 1) or max(len(first_row), len(second_row)) + 1
    candidates = []
    for index in range(group_start + 1, next_group_start):
        label = _cell(second_row, index).strip()
        if not label:
            continue
        if week_label and label != week_label:
            continue
        candidates.append(index)
    if not candidates:
        suffix = f" for week label {week_label}" if week_label else ""
        raise ValueError(f"Could not find weekly column in group '{group_query}'{suffix}.")
    return candidates[-1]


def _find_header_column(row: list[str], query: str) -> int:
    normalized_query = _normalize(query)
    for index, value in enumerate(row, start=1):
        if normalized_query in _normalize(value):
            return index
    raise ValueError(f"Could not find header column containing: {query}")


def _next_non_empty_index(row: list[str], start_index: int) -> int | None:
    for index in range(start_index, len(row) + 1):
        if _cell(row, index).strip():
            return index
    return None


def _format_legacy_milestone(milestone: str, milestone_date: str) -> str:
    text = (milestone or "").strip()
    date = (milestone_date or "").strip()
    if not text:
        return ""
    if not date or _starts_with_date(text):
        return text
    return f"{date} {text}"


def _starts_with_date(value: str) -> bool:
    return bool(
        re.match(
            r"^\s*(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2}|TBD)\b",
            value,
            flags=re.IGNORECASE,
        )
    )


def _sheet_value(row: list[str], header_index: dict[str, int], header: str) -> str:
    index = header_index.get(header)
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def _row_at(values: list[list[str]], index: int) -> list[str]:
    return values[index] if index < len(values) else []


def _cell(row: list[str], index: int) -> str:
    return str(row[index - 1]) if 0 < index <= len(row) else ""


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _preview(value: str) -> str:
    clean = " ".join(value.split())
    if len(clean) <= 90:
        return clean
    return clean[:87] + "..."
