from dataclasses import dataclass

from weekly_assistant.domain.enums import Lifecycle, MovementType, ReviewStatus, YesNo


@dataclass(frozen=True)
class WeeklySession:
    session_id: str
    week_label: str
    status: str
    source_ref: str


@dataclass(frozen=True)
class WeeklyRow:
    section: str
    topic_id: str
    topic_title: str
    date_created: str
    lifecycle: Lifecycle
    focus: YesNo
    previous_week_result: str
    current_week_facts: str
    ai_draft_result: str
    final_result: str
    next_milestone: str
    next_milestone_date: str
    ball_side: str
    open_question_to_evgeny: str
    movement_type: MovementType
    needs_sync: YesNo
    sync_reason: str
    source_links: str
    review_status: ReviewStatus

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    @property
    def is_focus(self) -> bool:
        return self.focus == YesNo.YES

    @property
    def has_open_question(self) -> bool:
        return bool(self.open_question_to_evgeny.strip())

    @property
    def sync_needed(self) -> bool:
        return self.needs_sync == YesNo.YES


@dataclass(frozen=True)
class ValidationIssue:
    row_number: int
    severity: str
    field: str
    message: str


@dataclass(frozen=True)
class WeeklySummary:
    total: int
    active: int
    paused: int
    needs_sync: int
    open_questions: int
    no_movement: int
    unclear: int


@dataclass(frozen=True)
class IncomingItem:
    source_type: str
    raw_text: str
    normalized_text: str
    proposed_destination: str
    clarification_question: str
