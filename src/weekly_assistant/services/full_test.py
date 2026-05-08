from dataclasses import dataclass
from pathlib import Path

from weekly_assistant.adapters.csv_adapter import load_weekly_rows, write_weekly_rows
from weekly_assistant.services.drafting_service import apply_rule_based_drafts
from weekly_assistant.services.exports import build_open_questions_export, build_summary, build_sync_export, format_summary
from weekly_assistant.services.validation import validate_rows
from weekly_assistant.services.weekly_engine import create_next_week_rows, refresh_computed_fields


@dataclass(frozen=True)
class FullTestResult:
    out_dir: Path
    source_csv: Path
    refreshed_csv: Path
    next_week_csv: Path
    drafted_csv: Path
    questions_txt: Path
    sync_txt: Path
    summary_txt: Path
    validation_txt: Path


def run_full_test(source_csv: str | Path, out_dir: str | Path) -> FullTestResult:
    source = Path(source_csv)
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)

    source_rows = load_weekly_rows(source)
    validation_issues = validate_rows(source_rows)
    validation_txt = output / "00_validation.txt"
    validation_txt.write_text(_format_validation(validation_issues), encoding="utf-8")

    refreshed_rows = [refresh_computed_fields(row) for row in source_rows]
    refreshed_csv = output / "01_refreshed.csv"
    write_weekly_rows(refreshed_csv, refreshed_rows)

    next_week_rows = create_next_week_rows(refreshed_rows)
    next_week_csv = output / "02_next_week.csv"
    write_weekly_rows(next_week_csv, next_week_rows)

    drafted_rows = apply_rule_based_drafts(next_week_rows)
    drafted_csv = output / "03_drafted.csv"
    write_weekly_rows(drafted_csv, drafted_rows)

    questions_txt = output / "questions.txt"
    questions_txt.write_text(build_open_questions_export(drafted_rows) + "\n", encoding="utf-8")

    sync_txt = output / "sync.txt"
    sync_txt.write_text(build_sync_export(drafted_rows) + "\n", encoding="utf-8")

    summary_txt = output / "summary.txt"
    summary_txt.write_text(format_summary(build_summary(drafted_rows)) + "\n", encoding="utf-8")

    return FullTestResult(
        out_dir=output,
        source_csv=source,
        refreshed_csv=refreshed_csv,
        next_week_csv=next_week_csv,
        drafted_csv=drafted_csv,
        questions_txt=questions_txt,
        sync_txt=sync_txt,
        summary_txt=summary_txt,
        validation_txt=validation_txt,
    )


def format_full_test_result(result: FullTestResult) -> str:
    return "\n".join(
        [
            f"Full test output: {result.out_dir}",
            f"Validation: {result.validation_txt}",
            f"Refreshed CSV: {result.refreshed_csv}",
            f"Next week CSV: {result.next_week_csv}",
            f"Drafted CSV: {result.drafted_csv}",
            f"Questions: {result.questions_txt}",
            f"Sync: {result.sync_txt}",
            f"Summary: {result.summary_txt}",
        ]
    )


def _format_validation(issues) -> str:
    if not issues:
        return "Validation passed.\n"
    lines = []
    for issue in issues:
        lines.append(f"{issue.severity.upper()} row {issue.row_number} [{issue.field}]: {issue.message}")
    return "\n".join(lines) + "\n"
