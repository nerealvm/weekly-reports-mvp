import csv
from pathlib import Path

from weekly_assistant.domain.enums import Lifecycle, MovementType, ReviewStatus, YesNo
from weekly_assistant.domain.models import WeeklyRow


HEADERS = {
    "section": "Секция",
    "topic_id": "Topic ID",
    "topic_title": "Тема",
    "date_created": "Дата постановки",
    "lifecycle": "Lifecycle",
    "focus": "Focus",
    "previous_week_result": "Результат прошлой недели",
    "current_week_facts": "Факты этой недели",
    "ai_draft_result": "AI draft result",
    "final_result": "Итоговая формулировка",
    "next_milestone": "Ближайшая веха",
    "next_milestone_date": "Дата вехи",
    "ball_side": "На чьей стороне мяч",
    "open_question_to_evgeny": "Открытый вопрос к Евгению",
    "movement_type": "Movement type",
    "needs_sync": "Нужен sync",
    "sync_reason": "Причина sync",
    "source_links": "Source / links",
    "review_status": "Review status",
}


def load_weekly_rows(csv_path: str | Path) -> list[WeeklyRow]:
    path = Path(csv_path)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = [header for header in HEADERS.values() if header not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(missing)}")
        return [_row_from_dict(row) for row in reader if _has_topic(row)]


def write_weekly_rows(csv_path: str | Path, rows: list[WeeklyRow]) -> None:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(HEADERS.values()))
        writer.writeheader()
        for row in rows:
            writer.writerow(_row_to_dict(row))


def _has_topic(row: dict[str, str]) -> bool:
    return bool((row.get(HEADERS["topic_title"]) or "").strip())


def _value(row: dict[str, str], key: str) -> str:
    return (row.get(HEADERS[key]) or "").strip()


def _enum(enum_type, value: str, default: str):
    normalized = (value or default).strip()
    try:
        return enum_type(normalized)
    except ValueError:
        return enum_type(default)


def _row_from_dict(row: dict[str, str]) -> WeeklyRow:
    return WeeklyRow(
        section=_value(row, "section"),
        topic_id=_value(row, "topic_id"),
        topic_title=_value(row, "topic_title"),
        date_created=_value(row, "date_created"),
        lifecycle=_enum(Lifecycle, _value(row, "lifecycle"), Lifecycle.ACTIVE.value),
        focus=_enum(YesNo, _value(row, "focus"), YesNo.NO.value),
        previous_week_result=_value(row, "previous_week_result"),
        current_week_facts=_value(row, "current_week_facts"),
        ai_draft_result=_value(row, "ai_draft_result"),
        final_result=_value(row, "final_result"),
        next_milestone=_value(row, "next_milestone"),
        next_milestone_date=_value(row, "next_milestone_date"),
        ball_side=_value(row, "ball_side"),
        open_question_to_evgeny=_value(row, "open_question_to_evgeny"),
        movement_type=_enum(MovementType, _value(row, "movement_type"), MovementType.UNCLEAR.value),
        needs_sync=_enum(YesNo, _value(row, "needs_sync"), YesNo.NO.value),
        sync_reason=_value(row, "sync_reason"),
        source_links=_value(row, "source_links"),
        review_status=_enum(ReviewStatus, _value(row, "review_status"), ReviewStatus.DRAFT.value),
    )


def _row_to_dict(row: WeeklyRow) -> dict[str, str]:
    return {
        HEADERS["section"]: row.section,
        HEADERS["topic_id"]: row.topic_id,
        HEADERS["topic_title"]: row.topic_title,
        HEADERS["date_created"]: row.date_created,
        HEADERS["lifecycle"]: row.lifecycle.value,
        HEADERS["focus"]: row.focus.value,
        HEADERS["previous_week_result"]: row.previous_week_result,
        HEADERS["current_week_facts"]: row.current_week_facts,
        HEADERS["ai_draft_result"]: row.ai_draft_result,
        HEADERS["final_result"]: row.final_result,
        HEADERS["next_milestone"]: row.next_milestone,
        HEADERS["next_milestone_date"]: row.next_milestone_date,
        HEADERS["ball_side"]: row.ball_side,
        HEADERS["open_question_to_evgeny"]: row.open_question_to_evgeny,
        HEADERS["movement_type"]: row.movement_type.value,
        HEADERS["needs_sync"]: row.needs_sync.value,
        HEADERS["sync_reason"]: row.sync_reason,
        HEADERS["source_links"]: row.source_links,
        HEADERS["review_status"]: row.review_status.value,
    }
