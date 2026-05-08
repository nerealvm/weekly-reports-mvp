from dataclasses import replace

from weekly_assistant.domain.enums import Lifecycle, MovementType, ReviewStatus, YesNo
from weekly_assistant.domain.models import WeeklyRow
from weekly_assistant.services.milestone_service import carry_forward_milestone
from weekly_assistant.services.movement_service import determine_movement
from weekly_assistant.services.sync_recommendation_service import recommend_sync_for_row


def refresh_computed_fields(row: WeeklyRow) -> WeeklyRow:
    movement_source = row.current_week_facts or row.final_result or row.ai_draft_result
    movement = determine_movement(movement_source)
    milestone, milestone_date = carry_forward_milestone(row.next_milestone)
    intermediate = replace(
        row,
        movement_type=movement,
        next_milestone=milestone,
        next_milestone_date=row.next_milestone_date or milestone_date,
    )
    needs_sync, sync_reason = recommend_sync_for_row(intermediate)
    return replace(intermediate, needs_sync=needs_sync, sync_reason=sync_reason)


def mark_reviewed(row: WeeklyRow) -> WeeklyRow:
    return replace(row, review_status=ReviewStatus.REVIEWED)


def set_focus(row: WeeklyRow, focused: bool) -> WeeklyRow:
    return replace(row, focus=YesNo.YES if focused else YesNo.NO)


def create_next_week_rows(rows: list[WeeklyRow], *, active_only: bool = True) -> list[WeeklyRow]:
    next_rows = []
    for row in rows:
        if active_only and row.lifecycle != Lifecycle.ACTIVE:
            continue
        if row.lifecycle in {Lifecycle.CLOSED, Lifecycle.ARCHIVED}:
            continue
        previous_result = row.final_result or row.ai_draft_result or row.current_week_facts or row.previous_week_result
        next_rows.append(
            replace(
                row,
                previous_week_result=previous_result,
                current_week_facts="",
                ai_draft_result="",
                final_result="",
                movement_type=MovementType.UNCLEAR,
                needs_sync=YesNo.NO,
                sync_reason="",
                review_status=ReviewStatus.DRAFT,
            )
        )
    return [refresh_computed_fields(row) for row in next_rows]
