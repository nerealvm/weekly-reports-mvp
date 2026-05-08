from dataclasses import dataclass

from weekly_assistant.domain.enums import Lifecycle
from weekly_assistant.domain.models import ValidationIssue, WeeklyRow
from weekly_assistant.services.validation import validate_rows


@dataclass(frozen=True)
class LaunchReadiness:
    ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: dict[str, int]


def check_launch_readiness(rows: list[WeeklyRow]) -> LaunchReadiness:
    issues = validate_rows(rows)
    errors = [_format_issue(issue) for issue in issues if issue.severity == "error"]
    warnings = [_format_issue(issue) for issue in issues if issue.severity != "error"]

    active_rows = [row for row in rows if row.lifecycle == Lifecycle.ACTIVE]
    metrics = {
        "total": len(rows),
        "active": len(active_rows),
        "paused": sum(1 for row in rows if row.lifecycle == Lifecycle.PAUSED),
        "active_without_milestone": sum(1 for row in active_rows if not row.next_milestone.strip()),
        "active_without_ball_side": sum(1 for row in active_rows if not row.ball_side.strip()),
        "open_questions": sum(1 for row in active_rows if row.has_open_question),
    }

    blocking = list(errors)
    if metrics["active"] == 0:
        blocking.append("No active topics found.")
    if metrics["active_without_ball_side"] > 0:
        blocking.append("Some active topics have empty ball side.")

    return LaunchReadiness(
        ready=not blocking,
        errors=tuple(blocking),
        warnings=tuple(warnings),
        metrics=metrics,
    )


def format_launch_readiness(readiness: LaunchReadiness) -> str:
    lines = ["READY" if readiness.ready else "NOT READY"]
    lines.append("Metrics:")
    for key, value in readiness.metrics.items():
        lines.append(f"- {key}: {value}")
    if readiness.errors:
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in readiness.errors)
    if readiness.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in readiness.warnings)
    return "\n".join(lines)


def _format_issue(issue: ValidationIssue) -> str:
    return f"row {issue.row_number} [{issue.field}]: {issue.message}"
