from weekly_assistant.domain.enums import Lifecycle, MovementType, YesNo
from weekly_assistant.domain.models import WeeklyRow


def recommend_sync_for_row(row: WeeklyRow) -> tuple[YesNo, str]:
    if row.lifecycle != Lifecycle.ACTIVE:
        return YesNo.NO, ""

    reasons = []
    if row.has_open_question:
        reasons.append("есть вопрос к Евгению")
    if "Евгений" in row.ball_side:
        reasons.append("мяч у Евгения")
    if row.is_focus and row.movement_type == MovementType.UNCLEAR:
        reasons.append("focus-тема с неясным движением")
    if row.is_focus and not row.next_milestone_date:
        reasons.append("focus-тема без даты вехи")

    return (YesNo.YES, "; ".join(reasons)) if reasons else (YesNo.NO, "")
