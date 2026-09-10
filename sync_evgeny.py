#!/usr/bin/env python3
"""
Выгрузка задач для синка с Евгением:
  - задачи с упоминанием Евгения (активные, в горизонте 30 дней)
  - раздел "Вопросы для синка" в проекте Пресняков Inc

Токен: установи переменную SINGULARITY_TOKEN или положи в ~/.singularity_token
Получить токен: Singularity → Профиль → API / Интеграции
"""

import os
import sys
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import json
import urllib.request

# ── конфиг ────────────────────────────────────────────────────────────────────

BASE_URL = "https://api.singularity-app.com"
PRESNYAKOV_PROJECT_ID = "P-59393df5-5069-471d-a875-3a805df6d309"
SYNC_GROUP_ID = "Q-0e0cb030-b2ba-412d-aeb5-bd9379ff1a22"  # "Вопросы для синка"

HORIZON_DAYS = 30  # задачи с датой старта в пределах N дней

# ── токен ─────────────────────────────────────────────────────────────────────

def get_token() -> str:
    token = os.environ.get("SINGULARITY_TOKEN", "").strip()
    if token:
        return token
    token_file = Path.home() / ".singularity_token"
    if token_file.exists():
        token = token_file.read_text().strip()
        if token:
            return token
    print("Токен не найден. Укажи одним из способов:")
    print("  export SINGULARITY_TOKEN=<твой_токен>")
    print(f"  echo '<твой_токен>' > {token_file}")
    print("Получить токен: Singularity → Профиль → Настройки → API")
    sys.exit(1)

# ── API ───────────────────────────────────────────────────────────────────────

def api_get(path: str, token: str, params: dict = None) -> dict:
    url = f"{BASE_URL}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

# ── фильтрация ────────────────────────────────────────────────────────────────

EVGENY_RE = re.compile(r"[Ее]вгени", re.IGNORECASE)
NOW = datetime.now(timezone.utc)
HORIZON = NOW + timedelta(days=HORIZON_DAYS)


def is_active(task: dict) -> bool:
    return not task.get("checked")


def within_horizon(task: dict) -> bool:
    start = task.get("start") or task.get("startDate")
    if not start:
        return True  # без даты — берём всегда
    try:
        d = datetime.fromisoformat(start.replace("Z", "+00:00"))
        return d <= HORIZON
    except ValueError:
        return True


def fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return ""

# ── вывод ─────────────────────────────────────────────────────────────────────

def print_section(title: str, tasks: list[dict]) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}  ({len(tasks)})")
    print(f"{'─' * 60}")
    if not tasks:
        print("  (пусто)")
        return
    for t in tasks:
        start = fmt_date(t.get("start") or t.get("startDate"))
        date_str = f" [{start}]" if start else ""
        print(f"  •{date_str} {t['title'].strip()}")

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    token = get_token()

    print(f"Загружаю задачи из Singularity…  (горизонт: {HORIZON_DAYS} дней)")

    # Все активные задачи
    data = api_get("/v2/task", token, params={"includeArchived": False})
    all_tasks = data.get("tasks", [])

    # 1. Упоминания Евгения — активные, в горизонте
    seen = set()
    evgeny_tasks = []
    for t in all_tasks:
        title = t.get("title", "")
        tid = t["id"]
        if tid in seen:
            continue
        seen.add(tid)
        if EVGENY_RE.search(title) and is_active(t) and within_horizon(t):
            evgeny_tasks.append(t)

    # Сортируем: сначала с датой, потом без
    def sort_key(t):
        s = t.get("start") or t.get("startDate") or ""
        return s or "9999"

    evgeny_tasks.sort(key=sort_key)

    # 2. Раздел "Вопросы для синка" в Пресняков Inc
    sync_tasks = [
        t for t in all_tasks
        if t.get("group") == SYNC_GROUP_ID and is_active(t)
    ]
    sync_tasks.sort(key=sort_key)

    # Вывод
    today = NOW.strftime("%d.%m.%Y")
    print(f"\n{'═' * 60}")
    print(f"  Синк с Евгением — {today}")
    print(f"{'═' * 60}")

    print_section("Задачи с упоминанием Евгения", evgeny_tasks)
    print_section("Пресняков Inc → Вопросы для синка", sync_tasks)
    print()


if __name__ == "__main__":
    main()
