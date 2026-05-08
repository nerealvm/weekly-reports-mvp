from weekly_assistant.domain.enums import Lifecycle, MovementType, ReviewStatus, YesNo
from weekly_assistant.domain.models import ValidationIssue, WeeklyRow

VALID_BALL_HINTS = {
    "Володя",
    "Евгений",
    "Внешняя сторона",
    "Исполнитель",
    "Исполнители",
    "Пауза",
    "На паузе",
    "Игорь",
    "Игорь, Володя",
    "Павел / бухгалтер",
    "Володя, Руслан",
    "Эльвира",
}

QUESTION_OR_REQUEST_HINTS = (
    "?",
    "дай ",
    "давай ",
    "нужен ",
    "нужна ",
    "нужно ",
    "подхватить",
    "сходишь",
    "обсудим",
    "жду обратной связи",
    "буду рад обратной связи",
)


def validate_rows(rows: list[WeeklyRow]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for index, row in enumerate(rows, start=2):
        issues.extend(_validate_row(index, row))
    return issues


def _validate_row(row_number: int, row: WeeklyRow) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not row.topic_title:
        issues.append(_issue(row_number, "error", "Тема", "empty topic title"))
    if row.lifecycle not in set(Lifecycle):
        issues.append(_issue(row_number, "error", "Lifecycle", "invalid lifecycle"))
    if row.focus not in set(YesNo):
        issues.append(_issue(row_number, "error", "Focus", "invalid focus value"))
    if row.movement_type not in set(MovementType):
        issues.append(_issue(row_number, "error", "Movement type", "invalid movement type"))
    if row.review_status not in set(ReviewStatus):
        issues.append(_issue(row_number, "error", "Review status", "invalid review status"))
    if row.lifecycle == Lifecycle.ACTIVE and not row.ball_side:
        issues.append(_issue(row_number, "warning", "На чьей стороне мяч", "active topic has empty ball side"))
    normalized_ball_side = " ".join(row.ball_side.split())
    if normalized_ball_side and normalized_ball_side not in VALID_BALL_HINTS:
        issues.append(_issue(row_number, "warning", "На чьей стороне мяч", "non-normalized ball side"))
    if row.lifecycle == Lifecycle.ACTIVE and not row.next_milestone:
        issues.append(_issue(row_number, "warning", "Ближайшая веха", "active topic has empty milestone"))
    if row.has_open_question and not _looks_like_question_or_request(row.open_question_to_evgeny):
        issues.append(_issue(row_number, "warning", "Открытый вопрос к Евгению", "open question may be a note, not a question"))
    return issues


def _issue(row_number: int, severity: str, field: str, message: str) -> ValidationIssue:
    return ValidationIssue(row_number=row_number, severity=severity, field=field, message=message)


def _looks_like_question_or_request(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    return any(hint in normalized for hint in QUESTION_OR_REQUEST_HINTS)
