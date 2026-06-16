# Weekly Assistant MVP

Локальный MVP для weekly-отчета Евгению.

Текущая Google Sheet остается source of truth. Основной weekly-сценарий работает от вкладки `Активные`: collector читает ее двухстрочную шапку, присваивает строкам сквозные `T-001`, `T-002`, ... на время сессии и пишет результат обратно в недельные колонки той же вкладки.

## Что уже поддержано

- чтение вкладки `Активные` или нормализованной weekly-таблицы из CSV;
- базовая валидация строк;
- выгрузка вопросов к Евгению;
- выгрузка кандидатов на sync;
- краткий summary по active / paused / no movement / unclear / sync.
- проверка готовности интеграций по env-переменным;
- каркас adapters для Google Sheets, Telegram Bot API, Telegram MTProto, SingularityApp и OpenAI Responses API;
- Singularity weekly-context по настраиваемому списку релевантных проектов.
- локальный browser-инструмент `Weekly Collector` для еженедельного сбора фактуры без работы в VS Code.

## Быстрый запуск

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli inspect --csv path/to/weekly_mvp.csv
PYTHONPATH=src python3 -m weekly_assistant.cli validate --csv path/to/weekly_mvp.csv
PYTHONPATH=src python3 -m weekly_assistant.cli refresh --csv path/to/weekly_mvp.csv --out exports/refreshed.csv
PYTHONPATH=src python3 -m weekly_assistant.cli create-week --csv exports/refreshed.csv --out exports/next_week.csv
PYTHONPATH=src python3 -m weekly_assistant.cli draft --csv exports/next_week.csv --out exports/drafted.csv
PYTHONPATH=src python3 -m weekly_assistant.cli export-questions --csv exports/drafted.csv --out exports/questions.txt
PYTHONPATH=src python3 -m weekly_assistant.cli export-sync --csv exports/drafted.csv --out exports/sync.txt
PYTHONPATH=src python3 -m weekly_assistant.cli integration-status
PYTHONPATH=src python3 -m weekly_assistant.cli singularity-weekly-context --week-start 2026-05-01 --week-end 2026-05-08 --out exports/singularity_context.md
```

## Проверка

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Или короче:

```bash
make test
make status
make sample-flow
make live-full-test
make singularity-context
make collector
```

`make collector` поднимает локальный web-инструмент:

```text
http://127.0.0.1:8765
```

В нем можно пройти темы из `Активные`, вставить сырой weekly dump, получить AI draft, импортировать JSON из ChatGPT Project и записать результат обратно в `Активные`. Collector создает недостающие недельные колонки в группах `Статус предыдущей недели`, `Куда мы докатились на этой`, `Когда докатимся и куда`, но не создает дополнительные MVP/Questions/Sync вкладки в основном сценарии.

`make viewer` поднимает read-only web-view для компактного просмотра weekly:

```text
http://127.0.0.1:8770
```

`make pages-export` обновляет статический fallback snapshot интерфейса Евгения для GitHub Pages:

```text
docs/report.json
```

GitHub Pages интерфейс живет в `docs/index.html` и `docs/src/`. Если Google Sheet доступна всем по ссылке, интерфейс читает текущую вкладку напрямую через публичный CSV endpoint `gviz/tq` без API key. Если публичный CSV недоступен, используется `docs/report.json`.

Обычный запуск продолжает локальную session. Для принудительного перечитывания Google Sheet используй:

```bash
make collector COLLECTOR_REFRESH=--refresh
```

## Документация

- `docs/architecture.md` — слои проекта.
- `docs/technical_spec.md` — текущее ТЗ для collector через `Активные`.
- `docs/workflows.md` — рабочий weekly-flow.
- `docs/integration_matrix.md` — проверенные API и ограничения.
- `docs/sheet_mapping.md` — mapping колонок вкладки `Активные`.
- `docs/first_launch.md` — инструкция первого запуска.
- `docs/credentials.md` — как получить и подключить API keys/tokens.
- `docs/chatgpt_weekly_project_prompt.md` — prompt для сбора weekly через ChatGPT UI и импорта JSON в collector.
- `docs/legacy_active_transfer.md` — старый перенос из нормализованной weekly-вкладки в `Активные` (legacy-сценарий).
- `docs/weekly_viewer.md` — read-only интерфейс для просмотра weekly.

## MVP-ограничение

Этот проект не предполагает, что доступы уже есть. Интеграции вынесены в adapters и включаются только при наличии env-переменных и подтвержденного сценария.
