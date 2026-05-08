from weekly_assistant.domain.models import WeeklyRow, WeeklySummary
from weekly_assistant.services.exports import build_summary, format_summary


def summarize(rows: list[WeeklyRow]) -> WeeklySummary:
    return build_summary(rows)


def summarize_text(rows: list[WeeklyRow]) -> str:
    return format_summary(build_summary(rows))
