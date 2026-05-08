from dataclasses import dataclass

from weekly_assistant.domain.enums import Lifecycle, MovementType, YesNo
from weekly_assistant.domain.models import WeeklyRow


@dataclass(frozen=True)
class WeeklyViewItem:
    topic_id: str
    topic_title: str
    section: str
    focus: str
    result: str
    milestones: str
    milestone_date: str
    ball_side: str
    open_question: str
    movement_type: str
    needs_sync: str
    sync_reason: str
    source_links: str
    priority: int


@dataclass(frozen=True)
class WeeklyViewModel:
    week_label: str
    source_sheet: str
    total_active: int
    real_result: int
    no_movement: int
    unclear: int
    needs_sync: int
    open_questions: int
    items: tuple[WeeklyViewItem, ...]


def build_weekly_view_model(rows: list[WeeklyRow], *, week_label: str, source_sheet: str) -> WeeklyViewModel:
    active = [row for row in rows if row.lifecycle == Lifecycle.ACTIVE]
    items = tuple(sorted((_view_item(row) for row in active), key=lambda item: (item.priority, item.section, item.topic_title)))
    return WeeklyViewModel(
        week_label=week_label,
        source_sheet=source_sheet,
        total_active=len(active),
        real_result=sum(1 for row in active if row.movement_type == MovementType.REAL_RESULT),
        no_movement=sum(1 for row in active if row.movement_type == MovementType.NO_MOVEMENT),
        unclear=sum(1 for row in active if row.movement_type == MovementType.UNCLEAR),
        needs_sync=sum(1 for row in active if row.needs_sync == YesNo.YES),
        open_questions=sum(1 for row in active if row.open_question_to_evgeny.strip()),
        items=items,
    )


def view_model_to_dict(model: WeeklyViewModel) -> dict:
    return {
        "week_label": model.week_label,
        "source_sheet": model.source_sheet,
        "metrics": {
            "total_active": model.total_active,
            "real_result": model.real_result,
            "no_movement": model.no_movement,
            "unclear": model.unclear,
            "needs_sync": model.needs_sync,
            "open_questions": model.open_questions,
        },
        "items": [
            {
                "topic_id": item.topic_id,
                "topic_title": item.topic_title,
                "section": item.section,
                "focus": item.focus,
                "result": item.result,
                "milestones": item.milestones,
                "milestone_date": item.milestone_date,
                "ball_side": item.ball_side,
                "open_question": item.open_question,
                "movement_type": item.movement_type,
                "needs_sync": item.needs_sync,
                "sync_reason": item.sync_reason,
                "source_links": item.source_links,
                "priority": item.priority,
            }
            for item in model.items
        ],
    }


def _view_item(row: WeeklyRow) -> WeeklyViewItem:
    return WeeklyViewItem(
        topic_id=row.topic_id,
        topic_title=row.topic_title,
        section=row.section,
        focus=row.focus.value,
        result=_clean_result(row),
        milestones=row.next_milestone,
        milestone_date=row.next_milestone_date,
        ball_side=_format_ball_side(row.ball_side),
        open_question=row.open_question_to_evgeny,
        movement_type=row.movement_type.value,
        needs_sync=row.needs_sync.value,
        sync_reason=row.sync_reason,
        source_links=row.source_links,
        priority=_priority(row),
    )


def _clean_result(row: WeeklyRow) -> str:
    return row.final_result or row.ai_draft_result or row.current_week_facts or "Без нового движения за неделю."


def _priority(row: WeeklyRow) -> int:
    if row.needs_sync == YesNo.YES:
        return 0
    if row.open_question_to_evgeny.strip():
        return 1
    if row.focus == YesNo.YES:
        return 2
    if row.movement_type == MovementType.REAL_RESULT:
        return 3
    if row.movement_type == MovementType.UNCLEAR:
        return 4
    return 5


def _format_ball_side(value: str) -> str:
    text = (value or "").strip()
    lower = text.casefold()
    if lower == "me":
        return "Володя"
    if lower == "evgeny":
        return "Евгений"
    if lower.startswith("external:"):
        suffix = text.split(":", 1)[1].strip()
        return suffix or "Внешняя сторона"
    return text
