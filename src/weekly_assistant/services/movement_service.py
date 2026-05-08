from weekly_assistant.domain.enums import MovementType


REAL_RESULT_HINTS = (
    "отдал",
    "передал",
    "получил",
    "согласовал",
    "зафиналил",
    "подготовил",
    "создал",
    "запустил",
    "решили",
    "договорились",
)

NO_MOVEMENT_HINTS = (
    "не двигал",
    "не было движения",
    "не занимался",
    "без движения",
)


def determine_movement(text: str) -> MovementType:
    normalized = text.lower().strip()
    if not normalized:
        return MovementType.UNCLEAR
    if any(hint in normalized for hint in NO_MOVEMENT_HINTS):
        return MovementType.NO_MOVEMENT
    if any(hint in normalized for hint in REAL_RESULT_HINTS):
        return MovementType.REAL_RESULT
    return MovementType.UNCLEAR
