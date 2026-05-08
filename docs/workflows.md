# MVP Workflows

## 1. Weekly по CSV

1. Export вкладки `Weekly MVP 2026-05-08` в CSV.
2. Проверить таблицу:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli validate --csv exports/weekly.csv
PYTHONPATH=src python3 -m weekly_assistant.cli launch-readiness --csv exports/weekly.csv
```

3. Пересчитать rule-based поля:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli refresh --csv exports/weekly.csv --out exports/weekly_refreshed.csv
```

4. Создать следующую неделю:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli create-week --csv exports/weekly_refreshed.csv --out exports/weekly_next.csv
```

5. Сделать rule-based draft:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli draft --csv exports/weekly_next.csv --out exports/weekly_drafted.csv
```

6. Получить вопросы к Евгению:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli export-questions --csv exports/weekly_drafted.csv --out exports/questions.txt
```

7. Получить темы для sync:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli export-sync --csv exports/weekly_drafted.csv --out exports/sync.txt
```

## 1.1. Полный локальный тест одной командой

Если есть CSV:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli full-test --csv exports/weekly.csv --out-dir exports/full-test
```

Если вкладка доступна для CSV export:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli download-sheet-csv \
  --spreadsheet-id 14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs \
  --gid 20260508 \
  --out exports/live.csv

PYTHONPATH=src python3 -m weekly_assistant.cli full-test --csv exports/live.csv --out-dir exports/full-test
```

## 2. Google Sheets API

MVP-путь остается CSV-first. Google Sheets adapter готов к подключению через:

- `GOOGLE_SHEETS_SPREADSHEET_ID`;
- `GOOGLE_OAUTH_ACCESS_TOKEN`, либо service account JSON + optional extra `.[google]`.

Write-back должен включаться только после ручной проверки mapping и политики перезаписи колонок.

## 3. Telegram

Bot API:

- использовать для команд помощника и ручного сбора фактуры;
- не использовать как источник полной истории чатов.

MTProto:

- использовать только если нужна история чатов;
- требует отдельного user-authorized flow.

Если `api_id`/`api_hash` получить не удалось:

1. Для текущей недели использовать `assisted visual review`.
2. Сначала зафиксировать scope:
   - период;
   - список чатов;
   - список тем/ключевых слов;
   - запрет на отправку сообщений и изменение чатов.
3. В каждом чате собрать только факты, пригодные для weekly:
   - предъявленный результат;
   - решение;
   - артефакт/ссылка;
   - открытый вопрос;
   - дата/следующий шаг;
   - на чьей стороне мяч.
4. Сохранить результат как обычный incoming input и прогнать через weekly pipeline.
5. На следующую неделю заменить visual review на `bot inbox` или Desktop export, если объем сообщений большой.

## 4. SingularityApp

Не завязывать weekly на недоступный shared project `Отдать Володе`.

MVP-подход:

- source для weekly: доступные личные/видимые проекты Singularity;
- список релевантных проектов хранится в `config/singularity_projects.csv`;
- `SINGULARITY_PROJECT_ID` остается только для ручных точечных команд;
- основной weekly-сбор: `singularity-weekly-context`.

Команда:

```bash
PYTHONPATH=src .venv/bin/python -m weekly_assistant.cli singularity-weekly-context \
  --week-start 2026-05-01 \
  --week-end 2026-05-07 \
  --out /tmp/weekly-live-test/singularity_context.md
```

Что собирается:

- задачи, отмеченные выполненными за период;
- открытые задачи в релевантных проектах;
- warnings по проектам, которые не удалось сопоставить.

Правило сопоставления:

- сначала `project_id`;
- затем точное `project_title`;
- затем substring `project_query`.

Если название проекта в Singularity поменялось, обновить строку в `config/singularity_projects.csv`.
