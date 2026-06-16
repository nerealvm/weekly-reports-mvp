# Workflows

## 1. Основной weekly-flow через `Активные`

1. Запустить collector на нужный отчетный период:

```bash
make collector WEEK_START=2026-06-05 WEEK_END=2026-06-12 COLLECTOR_REFRESH=--refresh
```

2. Открыть локальный интерфейс:

```text
http://127.0.0.1:8765
```

3. В collector пройти темы из вкладки `Активные`.

Варианты заполнения:

- вручную по каждой теме;
- через `Сырой dump` и `Разложить по темам`;
- через ChatGPT Project: `Copy ChatGPT context` -> prompt из `docs/chatgpt_weekly_project_prompt.md` -> импорт JSON обратно в collector.

4. Проверить строки глазами.
5. Нажать `Export` для локального preview.
6. Нажать `Write Active`, чтобы записать результат в Google Sheet.

`Write Active` пишет в существующую вкладку `Активные`:

- `Статус предыдущей недели`;
- `Куда мы докатились на этой`;
- `Когда докатимся и куда`;
- `На чьей стороне мяч`;
- `Открытые вопросы`.

Если недельной даты еще нет, collector создает ее в нужных трех группах.

## 2. Старт следующей недели

Внутри collector можно нажать `Start next week`.

Что происходит:

- текущий итог переносится в локальный `previous_week_result`;
- weekly-поля текущей сессии очищаются;
- период сдвигается на следующий friday-to-friday интервал;
- для `Активные` обновляется week label, который будет создан при `Write Active`.

После проверки нужно нажать `Write Active`.

## 3. Статический viewer

GitHub Pages viewer в `docs/index.html` читает `Активные` через публичный CSV endpoint:

- `spreadsheetId`: `14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs`
- `gid`: `0`
- `sheet`: `Активные`

Parser viewer-а берет последнюю дату из группы `Куда мы докатились на этой` и строит read-only отчет.

## 4. Legacy CSV-flow

Старый нормализованный CSV-flow через `Weekly MVP <date>` оставлен для совместимости тестов и ручных recovery-сценариев. Он не является основным способом заполнения отчета.

Если нужен именно legacy-flow, использовать CLI-команды из старых тестов и `docs/legacy_active_transfer.md`.

## 5. Singularity context

Singularity остается дополнительной подсказкой, а не source of truth.

```bash
make singularity-context WEEK_START=2026-06-05 WEEK_END=2026-06-12
```

Что собирается:

- задачи, отмеченные выполненными за период;
- открытые задачи в релевантных проектах;
- warnings по проектам, которые не удалось сопоставить.

Правило: подсказки Singularity можно использовать только как контекст. Они не должны подменять фактуру, которую пользователь явно подтверждает для weekly.
