import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


DEFAULT_SINGULARITY_PROJECTS_CONFIG = "config/singularity_projects.csv"


@dataclass(frozen=True)
class SingularityProjectMapping:
    enabled: bool
    topic: str
    project_id: str
    project_title: str
    project_query: str
    notes: str


@dataclass(frozen=True)
class MatchedProject:
    mapping: SingularityProjectMapping
    project_id: str
    project_title: str


@dataclass(frozen=True)
class SingularityTaskView:
    id: str
    title: str
    project_id: str
    project_title: str
    topic: str
    checked: bool
    done_date: date | None
    start_date: date | None
    deadline_date: date | None


@dataclass(frozen=True)
class SingularityWeeklyContext:
    week_start: date
    week_end: date
    matched_projects: list[MatchedProject]
    completed_this_week: list[SingularityTaskView]
    open_tasks: list[SingularityTaskView]
    warnings: list[str]


def load_project_mappings(path: str | Path = DEFAULT_SINGULARITY_PROJECTS_CONFIG) -> list[SingularityProjectMapping]:
    config_path = Path(path)
    if not config_path.exists():
        return []

    mappings: list[SingularityProjectMapping] = []
    with config_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            mappings.append(
                SingularityProjectMapping(
                    enabled=_is_enabled(row.get("enabled", "")),
                    topic=(row.get("topic") or "").strip(),
                    project_id=(row.get("project_id") or "").strip(),
                    project_title=(row.get("project_title") or "").strip(),
                    project_query=(row.get("project_query") or "").strip(),
                    notes=(row.get("notes") or "").strip(),
                )
            )
    return mappings


def build_singularity_weekly_context(
    adapter,
    *,
    config_path: str | Path = DEFAULT_SINGULARITY_PROJECTS_CONFIG,
    week_start: date,
    week_end: date,
    max_projects: int = 300,
    max_tasks_per_project: int = 100,
) -> SingularityWeeklyContext:
    mappings = [mapping for mapping in load_project_mappings(config_path) if mapping.enabled]
    project_response = adapter.list_projects(max_count=max_projects)
    projects = _dedupe_items(project_response.get("projects", []))
    matched_projects, warnings = _resolve_projects(mappings, projects)

    completed: list[SingularityTaskView] = []
    open_tasks: list[SingularityTaskView] = []

    for matched in matched_projects:
        try:
            task_response = adapter.list_tasks(
                project_id=matched.project_id,
                max_count=max_tasks_per_project,
                include_archived=True,
            )
        except Exception as exc:  # pragma: no cover - defensive boundary around remote API
            warnings.append(f"{matched.project_title}: failed to load tasks: {type(exc).__name__}: {exc}")
            continue

        for task in _dedupe_items(task_response.get("tasks", [])):
            view = _task_view(task, matched)
            if view.checked:
                if view.done_date and week_start <= view.done_date < week_end:
                    completed.append(view)
            else:
                open_tasks.append(view)

    completed.sort(key=lambda task: (task.project_title.casefold(), task.done_date or date.min, task.title.casefold()))
    open_tasks.sort(key=lambda task: (task.project_title.casefold(), task.start_date or date.max, task.title.casefold()))
    return SingularityWeeklyContext(
        week_start=week_start,
        week_end=week_end,
        matched_projects=matched_projects,
        completed_this_week=completed,
        open_tasks=open_tasks,
        warnings=warnings,
    )


def format_singularity_weekly_context(context: SingularityWeeklyContext) -> str:
    lines = [
        "# Singularity weekly context",
        "",
        f"Период: {context.week_start.isoformat()} - {context.week_end.isoformat()} (end exclusive)",
        f"Matched projects: {len(context.matched_projects)}",
        f"Completed this week: {len(context.completed_this_week)}",
        f"Open tasks: {len(context.open_tasks)}",
        "",
        "## Matched projects",
    ]
    if context.matched_projects:
        for project in context.matched_projects:
            topic = f" -> {project.mapping.topic}" if project.mapping.topic else ""
            lines.append(f"- {project.project_title} | id={project.project_id}{topic}")
    else:
        lines.append("- none")

    lines.extend(["", "## Completed this week"])
    _append_task_group(lines, context.completed_this_week, include_done_date=True)

    lines.extend(["", "## Open tasks"])
    _append_task_group(lines, context.open_tasks, include_done_date=False)

    if context.warnings:
        lines.extend(["", "## Warnings"])
        for warning in context.warnings:
            lines.append(f"- {warning}")

    return "\n".join(lines)


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _resolve_projects(
    mappings: list[SingularityProjectMapping],
    projects: list[dict],
) -> tuple[list[MatchedProject], list[str]]:
    by_id = {project.get("id", ""): project for project in projects if project.get("id")}
    by_title: dict[str, list[dict]] = {}
    for project in projects:
        title = _normalize(project.get("title", ""))
        if title:
            by_title.setdefault(title, []).append(project)

    matched: list[MatchedProject] = []
    warnings: list[str] = []
    for mapping in mappings:
        project = None
        if mapping.project_id:
            project = by_id.get(mapping.project_id)
            if not project:
                warnings.append(f"{mapping.topic or mapping.project_title}: project_id not visible: {mapping.project_id}")

        if not project and mapping.project_title:
            candidates = by_title.get(_normalize(mapping.project_title), [])
            if len(candidates) == 1:
                project = candidates[0]
            elif len(candidates) > 1:
                warnings.append(f"{mapping.topic or mapping.project_title}: multiple exact title matches: {mapping.project_title}")

        if not project and mapping.project_query:
            query = _normalize(mapping.project_query)
            candidates = [candidate for candidate in projects if query in _normalize(candidate.get("title", ""))]
            if len(candidates) == 1:
                project = candidates[0]
            elif len(candidates) > 1:
                titles = ", ".join(candidate.get("title", "") for candidate in candidates[:5])
                warnings.append(f"{mapping.topic or mapping.project_query}: ambiguous query '{mapping.project_query}': {titles}")

        if project:
            matched.append(
                MatchedProject(
                    mapping=mapping,
                    project_id=project.get("id", ""),
                    project_title=project.get("title", ""),
                )
            )
        else:
            warnings.append(f"{mapping.topic or mapping.project_title or mapping.project_query}: no project match")

    return matched, warnings


def _task_view(task: dict, matched: MatchedProject) -> SingularityTaskView:
    return SingularityTaskView(
        id=task.get("id", ""),
        title=(task.get("title") or "").strip(),
        project_id=matched.project_id,
        project_title=matched.project_title,
        topic=matched.mapping.topic,
        checked=_is_checked(task),
        done_date=_task_done_date(task),
        start_date=_date_from_iso(task.get("start", "")),
        deadline_date=_date_from_iso(task.get("deadline", "")),
    )


def _append_task_group(lines: list[str], tasks: list[SingularityTaskView], *, include_done_date: bool) -> None:
    if not tasks:
        lines.append("- none")
        return

    current_project = ""
    for task in tasks:
        if task.project_title != current_project:
            current_project = task.project_title
            lines.append(f"### {current_project}")
        meta = []
        if task.topic:
            meta.append(f"topic={task.topic}")
        if include_done_date and task.done_date:
            meta.append(f"done={task.done_date.isoformat()}")
        if not include_done_date and task.start_date:
            meta.append(f"start={task.start_date.isoformat()}")
        if not include_done_date and task.deadline_date:
            meta.append(f"deadline={task.deadline_date.isoformat()}")
        meta.append(f"id={task.id}")
        lines.append(f"- {task.title} ({'; '.join(meta)})")


def _task_done_date(task: dict) -> date | None:
    modified = task.get("modificated") or {}
    return (
        _date_from_epoch_ms(modified.get("checked"))
        or _date_from_epoch_ms(modified.get("completeLast"))
        or _date_from_epoch_ms(task.get("modificatedDate"))
    )


def _is_checked(task: dict) -> bool:
    value = task.get("checked")
    return value is True or value == 1 or value == "1"


def _date_from_epoch_ms(value) -> date | None:
    if value in (None, "", 0, 1, "0", "1"):
        return None
    try:
        timestamp_ms = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp_ms <= 1:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).date()


def _date_from_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _dedupe_items(items: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for item in items:
        item_id = item.get("id")
        if item_id and item_id in seen:
            continue
        if item_id:
            seen.add(item_id)
        deduped.append(item)
    return deduped


def _normalize(value: str) -> str:
    return " ".join(value.casefold().strip().split())


def _is_enabled(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "y", "да"}
