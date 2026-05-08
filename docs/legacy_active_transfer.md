# Legacy Active Transfer

Скрипт переносит данные из нормализованной weekly-вкладки в старую вкладку `Активные`.

Источник по умолчанию:

```text
Weekly MVP 2026-05-08
```

Цель по умолчанию:

```text
Активные
```

## Что переносит

Из weekly-вкладки:

- `Итоговая формулировка` -> старая колонка статуса недели в группе `Куда мы докатились на этой`;
- `Ближайшая веха` + `Дата вехи` -> старая колонка вех в группе `Когда докатимся и куда`;
- `На чьей стороне мяч` -> старая колонка `На чьей стороне мяч`;
- опционально `Открытый вопрос к Евгению` -> старая колонка `Открытые вопросы`.

Строки связываются через колонку `Legacy row` в weekly-вкладке.

## Dry-run

По умолчанию ничего не записывает:

```bash
make transfer-active
```

Можно явно указать дату колонки в старой вкладке:

```bash
make transfer-active TRANSFER_WEEK_LABEL=8.05
```

## Запись

После проверки dry-run:

```bash
make transfer-active TRANSFER_WEEK_LABEL=8.05 TRANSFER_APPLY=--apply
```

## Ручные overrides

Если автоопределение колонок ошиблось:

```bash
PYTHONPATH=src .venv/bin/python -m weekly_assistant.cli transfer-to-active \
  --status-col AC \
  --milestone-col AO \
  --ball-col AP \
  --apply
```

Чтобы также писать вопросы к Евгению:

```bash
make transfer-active TRANSFER_ARGS="--include-open-questions --question-col AQ" TRANSFER_APPLY=--apply
```

## Защита

- Без `--apply` запись не выполняется.
- Скрипт не чистит пустые значения в старой вкладке.
- Если в weekly-строке нет значения для поля, это поле не записывается.
- Переносятся только строки с `Lifecycle = active`.
- Строки без `Legacy row` пропускаются.
