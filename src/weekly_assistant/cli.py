import argparse
import sys
from pathlib import Path

from weekly_assistant.adapters.csv_adapter import load_weekly_rows, write_weekly_rows
from weekly_assistant.adapters.google_csv_export import download_sheet_csv
from weekly_assistant.config.settings import load_settings
from weekly_assistant.integrations.google_sheets import GoogleSheetsAdapter
from weekly_assistant.integrations.status import collect_integration_status, format_integration_status
from weekly_assistant.integrations.telegram_bot import TelegramBotAdapter
from weekly_assistant.integrations.singularity import SingularityAdapter
from weekly_assistant.services.drafting_service import apply_rule_based_drafts
from weekly_assistant.services.exports import (
    build_open_questions_export,
    build_summary,
    build_sync_export,
    format_summary,
)
from weekly_assistant.services.full_test import format_full_test_result, run_full_test
from weekly_assistant.services.launch_readiness import check_launch_readiness, format_launch_readiness
from weekly_assistant.services.legacy_active_transfer import (
    DEFAULT_TARGET_SHEET,
    format_legacy_transfer_result,
    transfer_weekly_to_active,
)
from weekly_assistant.services.singularity_access import check_project_access, format_project_access
from weekly_assistant.services.singularity_formatters import (
    filter_items_by_query,
    format_project_items,
    format_singularity_projects,
    format_singularity_tasks,
    format_task_items,
)
from weekly_assistant.services.singularity_weekly_context import (
    DEFAULT_SINGULARITY_PROJECTS_CONFIG,
    build_singularity_weekly_context,
    format_singularity_weekly_context,
    parse_date,
)
from weekly_assistant.services.validation import validate_rows
from weekly_assistant.services.weekly_engine import create_next_week_rows, refresh_computed_fields


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weekly-assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ["inspect", "summary", "export-questions", "export-sync", "validate", "launch-readiness"]:
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--csv", required=True, help="CSV export of Weekly MVP sheet")
    export_questions = subparsers.choices["export-questions"]
    export_questions.add_argument("--out", help="Optional text output path")
    export_sync = subparsers.choices["export-sync"]
    export_sync.add_argument("--out", help="Optional text output path")

    refresh = subparsers.add_parser("refresh")
    refresh.add_argument("--csv", required=True, help="CSV export of Weekly MVP sheet")
    refresh.add_argument("--out", required=True, help="Output CSV path")

    draft = subparsers.add_parser("draft")
    draft.add_argument("--csv", required=True, help="CSV export of Weekly MVP sheet")
    draft.add_argument("--out", required=True, help="Output CSV path")
    draft.add_argument("--finalize", action="store_true", help="Copy draft into final result when final is empty")

    create_week = subparsers.add_parser("create-week")
    create_week.add_argument("--csv", required=True, help="CSV export of current Weekly MVP sheet")
    create_week.add_argument("--out", required=True, help="Output CSV path for the next week draft")
    create_week.add_argument("--include-paused", action="store_true", help="Carry paused rows too")

    full_test = subparsers.add_parser("full-test")
    full_test.add_argument("--csv", required=True, help="CSV export of current Weekly MVP sheet")
    full_test.add_argument("--out-dir", required=True, help="Directory for full-test artifacts")

    download_csv = subparsers.add_parser("download-sheet-csv")
    download_csv.add_argument("--spreadsheet-id", required=True)
    download_csv.add_argument("--gid", required=True)
    download_csv.add_argument("--out", required=True)

    transfer_active = subparsers.add_parser("transfer-to-active")
    transfer_active.add_argument("--source-sheet", default="", help="Normalized Weekly MVP sheet name")
    transfer_active.add_argument("--target-sheet", default=DEFAULT_TARGET_SHEET, help="Legacy active sheet name")
    transfer_active.add_argument("--week-label", default="", help="Exact date label in legacy row 2, e.g. 8.05")
    transfer_active.add_argument("--status-col", default="", help="Override legacy status column, e.g. AC")
    transfer_active.add_argument("--milestone-col", default="", help="Override legacy milestone column, e.g. AO")
    transfer_active.add_argument("--ball-col", default="", help="Override legacy ball-side column, e.g. AP")
    transfer_active.add_argument("--question-col", default="", help="Override legacy open question column, e.g. AQ")
    transfer_active.add_argument("--include-open-questions", action="store_true")
    transfer_active.add_argument("--apply", action="store_true", help="Actually write to Google Sheets. Without this flag dry-run only.")

    subparsers.add_parser("integration-status")
    subparsers.add_parser("telegram-get-me")

    singularity_tasks = subparsers.add_parser("singularity-list-tasks")
    singularity_tasks.add_argument("--max-count", type=int, default=20)
    singularity_tasks.add_argument("--project-id", default="")
    singularity_tasks.add_argument("--query", default="", help="Filter returned tasks by text")
    singularity_tasks.add_argument("--json", action="store_true", help="Print raw API response")

    singularity_projects = subparsers.add_parser("singularity-list-projects")
    singularity_projects.add_argument("--max-count", type=int, default=100)
    singularity_projects.add_argument("--query", default="", help="Filter returned projects by text")
    singularity_projects.add_argument("--json", action="store_true", help="Print raw API response")

    singularity_check_project = subparsers.add_parser("singularity-check-project")
    singularity_check_project.add_argument("--project-id", required=True)

    singularity_weekly_context = subparsers.add_parser("singularity-weekly-context")
    singularity_weekly_context.add_argument("--config", default="")
    singularity_weekly_context.add_argument("--week-start", required=True, help="Inclusive start date, YYYY-MM-DD")
    singularity_weekly_context.add_argument("--week-end", required=True, help="Inclusive end date, YYYY-MM-DD")
    singularity_weekly_context.add_argument("--max-tasks-per-project", type=int, default=100)
    singularity_weekly_context.add_argument("--out", help="Optional markdown output path")

    args = parser.parse_args(argv)
    settings = load_settings()
    if args.command == "integration-status":
        statuses = collect_integration_status(settings)
        print(format_integration_status(statuses))
        return 0
    if args.command == "telegram-get-me":
        print(TelegramBotAdapter(settings).get_me())
        return 0
    if args.command == "singularity-list-tasks":
        response = SingularityAdapter(settings).list_tasks(project_id=args.project_id, max_count=args.max_count)
        if args.json:
            print(response)
        elif args.query:
            print(format_task_items(filter_items_by_query(response.get("tasks", []), args.query)))
        else:
            print(format_singularity_tasks(response))
        return 0
    if args.command == "singularity-list-projects":
        response = SingularityAdapter(settings).list_projects(max_count=args.max_count)
        if args.json:
            print(response)
        elif args.query:
            print(format_project_items(filter_items_by_query(response.get("projects", []), args.query)))
        else:
            print(format_singularity_projects(response))
        return 0
    if args.command == "singularity-check-project":
        access = check_project_access(SingularityAdapter(settings), args.project_id)
        print(format_project_access(access))
        return 0
    if args.command == "singularity-weekly-context":
        context = build_singularity_weekly_context(
            SingularityAdapter(settings),
            config_path=args.config or settings.singularity_projects_config or DEFAULT_SINGULARITY_PROJECTS_CONFIG,
            week_start=parse_date(args.week_start),
            week_end=parse_date(args.week_end),
            max_tasks_per_project=args.max_tasks_per_project,
        )
        _emit_text(format_singularity_weekly_context(context), args.out)
        return 0
    if args.command == "download-sheet-csv":
        path = download_sheet_csv(args.spreadsheet_id, args.gid, args.out)
        print(f"Downloaded CSV: {path}")
        return 0
    if args.command == "transfer-to-active":
        result = transfer_weekly_to_active(
            GoogleSheetsAdapter(settings),
            source_sheet=args.source_sheet or settings.google_sheets_weekly_sheet_name,
            target_sheet=args.target_sheet,
            week_label=args.week_label,
            status_col=args.status_col,
            milestone_col=args.milestone_col,
            ball_col=args.ball_col,
            question_col=args.question_col,
            include_open_questions=args.include_open_questions,
            apply=args.apply,
        )
        print(format_legacy_transfer_result(result))
        return 0

    rows = load_weekly_rows(args.csv)

    if args.command == "inspect":
        print(format_summary(build_summary(rows)))
        return 0
    if args.command == "summary":
        print(format_summary(build_summary(rows)))
        return 0
    if args.command == "export-questions":
        _emit_text(build_open_questions_export(rows), args.out)
        return 0
    if args.command == "export-sync":
        _emit_text(build_sync_export(rows), args.out)
        return 0
    if args.command == "validate":
        issues = validate_rows(rows)
        if not issues:
            print("Validation passed.")
            return 0
        for issue in issues:
            print(f"{issue.severity.upper()} row {issue.row_number} [{issue.field}]: {issue.message}")
        return 1 if any(issue.severity == "error" for issue in issues) else 0
    if args.command == "launch-readiness":
        readiness = check_launch_readiness(rows)
        print(format_launch_readiness(readiness))
        return 0 if readiness.ready else 1
    if args.command == "refresh":
        write_weekly_rows(args.out, [refresh_computed_fields(row) for row in rows])
        print(f"Wrote refreshed CSV: {args.out}")
        return 0
    if args.command == "draft":
        write_weekly_rows(args.out, apply_rule_based_drafts(rows, finalize=args.finalize))
        print(f"Wrote drafted CSV: {args.out}")
        return 0
    if args.command == "create-week":
        next_rows = create_next_week_rows(rows, active_only=not args.include_paused)
        write_weekly_rows(args.out, next_rows)
        print(f"Wrote next week CSV: {args.out}")
        return 0
    if args.command == "full-test":
        result = run_full_test(args.csv, args.out_dir)
        print(format_full_test_result(result))
        return 0

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


def _emit_text(text: str, out_path: str | None) -> None:
    if not out_path:
        print(text)
        return
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote text export: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
