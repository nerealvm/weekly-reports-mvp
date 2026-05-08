from urllib.parse import urljoin

from weekly_assistant.config.settings import Settings
from weekly_assistant.integrations.base import IntegrationStatus
from weekly_assistant.utils.http import JsonHttpClient


class SingularityAdapter:
    name = "singularity"

    def __init__(self, settings: Settings, http_client: JsonHttpClient | None = None):
        self.settings = settings
        self.http_client = http_client or JsonHttpClient()

    def status(self) -> IntegrationStatus:
        missing = []
        if not self.settings.singularity_api_token:
            missing.append("SINGULARITY_API_TOKEN")
        if not self.settings.singularity_base_url:
            missing.append("SINGULARITY_BASE_URL")
        return IntegrationStatus(
            name=self.name,
            configured=not missing,
            mode="task/project polling",
            missing=tuple(missing),
            notes="API supports CRUD but not webhooks/event streams; use polling for 'Отдать Володе'.",
        )

    def list_tasks(
        self,
        *,
        project_id: str | None = None,
        max_count: int = 100,
        offset: int = 0,
        include_archived: bool = False,
        include_removed: bool = False,
    ) -> dict:
        params = {
            "projectId": project_id or self.settings.singularity_project_id,
            "maxCount": max_count,
            "offset": offset,
            "includeArchived": str(include_archived).lower(),
            "includeRemoved": str(include_removed).lower(),
        }
        return self._request("GET", "/v2/task", params=params)

    def list_projects(self, *, max_count: int = 100, offset: int = 0, include_archived: bool = False) -> dict:
        params = {
            "maxCount": max_count,
            "offset": offset,
            "includeArchived": str(include_archived).lower(),
            "includeRemoved": "false",
        }
        return self._request("GET", "/v2/project", params=params)

    def create_task(self, title: str, *, note: str = "", start: str = "", project_id: str = "") -> dict:
        payload = {"title": title}
        if note:
            payload["note"] = note
        if start:
            payload["start"] = start
        if project_id or self.settings.singularity_project_id:
            payload["projectId"] = project_id or self.settings.singularity_project_id
        return self._request("POST", "/v2/task", payload=payload)

    def _request(self, method: str, path: str, *, params: dict | None = None, payload: dict | None = None) -> dict:
        if not self.settings.singularity_api_token:
            raise RuntimeError("SINGULARITY_API_TOKEN is not configured.")
        url = urljoin(self.settings.singularity_base_url.rstrip("/") + "/", path.lstrip("/"))
        headers = {"Authorization": f"Bearer {self.settings.singularity_api_token}"}
        return self.http_client.request(method, url, headers=headers, params=params, payload=payload)
