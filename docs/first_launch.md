# First Launch

Дата подготовки: 2026-05-06.

## Статус

Первый локальный запуск готов.

Проверенная вкладка:

- spreadsheet: `14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs`
- sheet: `Weekly MVP 2026-05-08`
- gid: `20260508`

## Команда полного запуска

```bash
make live-full-test
```

Что делает команда:

1. скачивает CSV из `Weekly MVP 2026-05-08`;
2. проверяет launch readiness;
3. пересчитывает rule-based поля;
4. создает draft следующей недели;
5. генерирует draft строк;
6. выгружает вопросы к Евгению;
7. выгружает кандидатов на sync;
8. сохраняет summary.

## Проверенные результаты

Последний прогон:

```text
Validation passed.
READY
total: 33
active: 24
paused: 9
active_without_milestone: 0
active_without_ball_side: 0
open_questions: 12
```

Итоговый draft следующей недели:

```text
Всего тем: 24
Active: 24
Paused: 0
Нужен sync: 13
Открытых вопросов: 12
No movement: 16
Unclear: 8
```

## Артефакты запуска

```text
/tmp/weekly-live-test/source.csv
/tmp/weekly-live-test/00_validation.txt
/tmp/weekly-live-test/01_refreshed.csv
/tmp/weekly-live-test/02_next_week.csv
/tmp/weekly-live-test/03_drafted.csv
/tmp/weekly-live-test/questions.txt
/tmp/weekly-live-test/sync.txt
/tmp/weekly-live-test/summary.txt
/tmp/weekly-live-test/singularity_context.md
```

## Singularity context

Если Singularity token настроен, можно отдельно собрать контекст по релевантным проектам:

```bash
make singularity-context WEEK_START=2026-05-01 WEEK_END=2026-05-07
```

Конфиг проектов:

```text
config/singularity_projects.csv
```

Этот режим не использует недоступный shared project `Отдать Володе`; он работает по списку доступных проектов и вытаскивает выполненные за неделю задачи плюс открытые задачи.

## Что проверить глазами перед отправкой Евгению

- `questions.txt`: убрать вопросы, которые на самом деле не надо отправлять Евгению.
- `sync.txt`: решить, нужен один общий sync или точечные ответы по вопросам.
- `03_drafted.csv`: заменить rule-based drafts фактическими weekly-формулировками там, где есть новая фактура.
- `singularity_context.md`: использовать как источник фактуры по выполненным задачам и следующим действиям.

## Что не входит в первый запуск

- автоматический write-back в Google Sheets;
- автоматический Telegram ingestion;
- автоматическое внесение Singularity context обратно в строки weekly.

Для этих сценариев нужны ключи в `.env` и отдельный интеграционный тест.
