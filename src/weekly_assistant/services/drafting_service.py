from dataclasses import dataclass, replace

from weekly_assistant.domain.enums import MovementType
from weekly_assistant.domain.models import WeeklyRow


@dataclass(frozen=True)
class DraftResult:
    result_text: str
    movement_type: MovementType
    next_milestone: str
    next_milestone_date: str
    ball_side: str
    open_question_to_evgeny: str
    explanation: str


class RuleBasedDraftingService:
    """Fallback drafting path for MVP when no LLM provider is configured."""

    def draft_from_existing_row(self, row: WeeklyRow) -> DraftResult:
        if row.current_week_facts:
            result_text = row.current_week_facts
            movement = MovementType.REAL_RESULT
            explanation = "Used current-week facts as the draft because no LLM provider is configured."
        elif row.previous_week_result:
            result_text = "Без нового движения. Прошлый результат сохранен как контекст."
            movement = MovementType.NO_MOVEMENT
            explanation = "No current-week facts were provided."
        else:
            result_text = "Нужна фактура по теме."
            movement = MovementType.UNCLEAR
            explanation = "No previous result or current-week facts were available."

        return DraftResult(
            result_text=result_text,
            movement_type=movement,
            next_milestone=row.next_milestone,
            next_milestone_date=row.next_milestone_date,
            ball_side=row.ball_side,
            open_question_to_evgeny=row.open_question_to_evgeny,
            explanation=explanation,
        )


def apply_rule_based_drafts(rows: list[WeeklyRow], *, finalize: bool = False) -> list[WeeklyRow]:
    service = RuleBasedDraftingService()
    updated = []
    for row in rows:
        draft = service.draft_from_existing_row(row)
        final_result = row.final_result
        if finalize and not final_result:
            final_result = draft.result_text
        updated.append(
            replace(
                row,
                ai_draft_result=row.ai_draft_result or draft.result_text,
                final_result=final_result,
                movement_type=draft.movement_type,
                next_milestone=row.next_milestone or draft.next_milestone,
                next_milestone_date=row.next_milestone_date or draft.next_milestone_date,
                ball_side=row.ball_side or draft.ball_side,
                open_question_to_evgeny=row.open_question_to_evgeny or draft.open_question_to_evgeny,
            )
        )
    return updated
