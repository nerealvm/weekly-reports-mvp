import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    google_sheets_spreadsheet_id: str
    google_sheets_weekly_gid: str
    google_sheets_weekly_sheet_name: str
    google_oauth_access_token: str
    google_application_credentials: str
    telegram_bot_token: str
    telegram_api_id: str
    telegram_api_hash: str
    telegram_session_name: str
    singularity_api_token: str
    singularity_base_url: str
    singularity_project_id: str
    singularity_projects_config: str
    openai_api_key: str
    openai_model: str


def load_settings(env_file: str | Path = ".env") -> Settings:
    env = _merged_env(env_file)
    return Settings(
        google_sheets_spreadsheet_id=env.get("GOOGLE_SHEETS_SPREADSHEET_ID", ""),
        google_sheets_weekly_gid=env.get("GOOGLE_SHEETS_WEEKLY_GID", "20260508"),
        google_sheets_weekly_sheet_name=env.get("GOOGLE_SHEETS_WEEKLY_SHEET_NAME", "Weekly MVP 2026-05-08"),
        google_oauth_access_token=env.get("GOOGLE_OAUTH_ACCESS_TOKEN", ""),
        google_application_credentials=env.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_api_id=env.get("TELEGRAM_API_ID", ""),
        telegram_api_hash=env.get("TELEGRAM_API_HASH", ""),
        telegram_session_name=env.get("TELEGRAM_SESSION_NAME", "weekly_assistant"),
        singularity_api_token=env.get("SINGULARITY_API_TOKEN", ""),
        singularity_base_url=env.get("SINGULARITY_BASE_URL", "https://api.singularity-app.com"),
        singularity_project_id=env.get("SINGULARITY_PROJECT_ID", ""),
        singularity_projects_config=env.get("SINGULARITY_PROJECTS_CONFIG", "config/singularity_projects.csv"),
        openai_api_key=env.get("OPENAI_API_KEY", ""),
        openai_model=env.get("OPENAI_MODEL", ""),
    )


def _merged_env(env_file: str | Path) -> dict[str, str]:
    file_env = _read_env_file(Path(env_file))
    return {**file_env, **os.environ}


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_quotes(value.strip())
    return values


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
