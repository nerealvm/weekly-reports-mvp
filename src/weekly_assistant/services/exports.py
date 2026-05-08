from weekly_assistant.domain.enums import Lifecycle, MovementType
from weekly_assistant.domain.models import WeeklyRow, WeeklySummary


def active_rows(rows: list[WeeklyRow]) -> list[WeeklyRow]:
    return [row for row in rows if row.lifecycle == Lifecycle.ACTIVE]


def build_open_questions_export(rows: list[WeeklyRow]) -> str:
    candidates = [row for row in active_rows(rows) if row.has_open_question]
    if not candidates:
        return "Открытых вопросов к Евгению нет."
    blocks = []
    for row in candidates:
        source = f"\nИсточник: {row.source_links}" if row.source_links else ""
        blocks.append(f"Тема: {row.topic_title}\nВопрос: {row.open_question_to_evgeny}{source}")
    return "\n\n".join(blocks)


def build_sync_export(rows: list[WeeklyRow]) -> str:
    candidates = [row for row in active_rows(rows) if row.sync_needed]
    if not candidates:
        return "Sync по текущим правилам не требуется."
    lines = []
    for row in candidates:
        reason = row.sync_reason or "reason not specified"
        lines.append(f"- {row.topic_title}: {reason}")
    return "\n".join(lines)


def build_summary(rows: list[WeeklyRow]) -> WeeklySummary:
    active = active_rows(rows)
    return WeeklySummary(
        total=len(rows),
        active=len(active),
        paused=sum(1 for row in rows if row.lifecycle == Lifecycle.PAUSED),
        needs_sync=sum(1 for row in active if row.sync_needed),
        open_questions=sum(1 for row in active if row.has_open_question),
        no_movement=sum(1 for row in active if row.movement_type == MovementType.NO_MOVEMENT),
        unclear=sum(1 for row in active if row.movement_type == MovementType.UNCLEAR),
    )


def format_summary(summary: WeeklySummary) -> str:
    return "\n".join(
        [
            f"Всего тем: {summary.total}",
            f"Active: {summary.active}",
            f"Paused: {summary.paused}",
            f"Нужен sync: {summary.needs_sync}",
            f"Открытых вопросов: {summary.open_questions}",
            f"No movement: {summary.no_movement}",
            f"Unclear: {summary.unclear}",
        ]
    )
