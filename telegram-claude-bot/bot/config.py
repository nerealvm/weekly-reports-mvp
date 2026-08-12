"""Конфигурация бота — всё через переменные окружения (.env)."""

import os
from pathlib import Path


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Не задана переменная окружения {name} — см. .env.example")
    return value


TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")

# Кто может пользоваться ботом: список Telegram user id через запятую.
ALLOWED_USER_IDS = {
    int(x) for x in _require("ALLOWED_USER_IDS").replace(" ", "").split(",") if x
}

# Модель для Claude Agent SDK (алиас или полный id).
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5")

# Рабочая директория агента и база сессий (в docker — том /data).
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
WORKDIR = DATA_DIR / "workspace"
# Присланные файлы кладём внутрь рабочей директории агента, чтобы он их видел.
MEDIA_DIR = WORKDIR / "incoming"
DB_PATH = DATA_DIR / "bot.db"

# Модель whisper для расшифровки голосовых: tiny/base/small/medium/large-v3.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")

# Максимум секунд на один прогон агента (защита от зависания).
CLAUDE_TIMEOUT_SEC = int(os.environ.get("CLAUDE_TIMEOUT_SEC", "900"))

for d in (WORKDIR, MEDIA_DIR):
    d.mkdir(parents=True, exist_ok=True)
