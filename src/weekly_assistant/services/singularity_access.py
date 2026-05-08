from dataclasses import dataclass
from typing import Any

from weekly_assistant.integrations.singularity import SingularityAdapter
from weekly_assistant.utils.http import JsonHttpError


@dataclass(frozen=True)
class SingularityProjectAccess:
    project_id: str
    visible_in_project_list: bool
    listed_project_title: str
    active_task_count: int
    archived_or_removed_task_count: int
    direct_lookup_status: str


def check_project_access(adapter: SingularityAdapter, project_id: str) -> SingularityProjectAccess:
    projects_response = adapter.list_projects(max_count=1000, include_archived=True)
    projects = projects_response.get("projects", [])
    matched_project = next((project for project in projects if project.get("id") == project_id), None)

    active_tasks = adapter.list_tasks(
        project_id=project_id,
        max_count=100,
        include_archived=False,
        include_removed=False,
    ).get("tasks", [])
    archived_or_removed_tasks = adapter.list_tasks(
        project_id=project_id,
        max_count=100,
        include_archived=True,
        include_removed=True,
    ).get("tasks", [])

    return SingularityProjectAccess(
        project_id=project_id,
        visible_in_project_list=matched_project is not None,
        listed_project_title=(matched_project or {}).get("title", ""),
        active_task_count=len(_dedupe(active_tasks)),
        archived_or_removed_task_count=len(_dedupe(archived_or_removed_tasks)),
        direct_lookup_status=_direct_lookup_status(adapter, project_id),
    )


def format_project_access(access: SingularityProjectAccess) -> str:
    lines = [
        f"project_id: {access.project_id}",
        f"visible_in_project_list: {access.visible_in_project_list}",
        f"listed_project_title: {access.listed_project_title or '-'}",
        f"active_task_count: {access.active_task_count}",
        f"archived_or_removed_task_count: {access.archived_or_removed_task_count}",
        f"direct_lookup_status: {access.direct_lookup_status}",
    ]
    if not access.visible_in_project_list and access.active_task_count == 0 and access.archived_or_removed_task_count == 0:
        lines.append("conclusion: no readable API access detected for this project id")
    elif access.active_task_count > 0 or access.archived_or_removed_task_count > 0:
        lines.append("conclusion: project tasks are readable via API")
    else:
        lines.append("conclusion: project is visible, but no tasks were returned")
    return "\n".join(lines)


def _direct_lookup_status(adapter: SingularityAdapter, project_id: str) -> str:
    try:
        adapter._request("GET", f"/v2/project/{project_id}")
    except JsonHttpError as exc:
        return f"error status={exc.status}"
    except Exception as exc:
        return f"error {type(exc).__name__}"
    return "ok"


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        item_id = item.get("id")
        if item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result
