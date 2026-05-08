from typing import Any


def format_singularity_projects(response: dict[str, Any]) -> str:
    return format_project_items(_items(response, "projects"))


def format_project_items(projects: list[dict[str, Any]]) -> str:
    if not projects:
        return "No Singularity projects returned."
    lines = []
    for project in projects:
        project_id = project.get("id", "")
        title = project.get("title", "")
        state = project.get("state", "")
        lines.append(f"- {title} | id={project_id} | state={state}")
    return "\n".join(lines)


def format_singularity_tasks(response: dict[str, Any]) -> str:
    return format_task_items(_items(response, "tasks"))


def format_task_items(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "No Singularity tasks returned."
    lines = []
    seen = set()
    for task in tasks:
        task_id = task.get("id", "")
        if task_id in seen:
            continue
        seen.add(task_id)
        title = task.get("title", "")
        project_id = task.get("projectId", "")
        state = task.get("state", "")
        start = task.get("start", "")
        lines.append(f"- {title} | id={task_id} | project={project_id} | state={state} | start={start}")
    return "\n".join(lines)


def _items(response: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = response.get(key, [])
    return value if isinstance(value, list) else []


def filter_items_by_query(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    normalized_query = query.lower().strip()
    if not normalized_query:
        return items
    return [item for item in items if normalized_query in _searchable_text(item)]


def _searchable_text(item: dict[str, Any]) -> str:
    fields = [item.get("title", ""), item.get("note", ""), item.get("id", ""), item.get("projectId", "")]
    return " ".join(str(field).lower() for field in fields if field is not None)
