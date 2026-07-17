# CLAUDE.md — Weekly Assistant

Полуавтоматический сборщик еженедельного отчёта для Евгения. Python 3.11+, CLI + локальный веб-collector, source of truth — вкладка `Активные` в Google Sheet.

## Запуск и проверка

- Тесты: `make test` (это `python -m unittest discover -s tests`).
- Всегда работать из venv: `source .venv/bin/activate`. CLI ставится как `weekly-assistant`; если запуск через модуль — нужен `PYTHONPATH=src`.
- Локальный collector: `make collector` → http://127.0.0.1:8765. Перечитать Sheet принудительно: `make collector COLLECTOR_REFRESH=--refresh`.
- Read-only просмотр: `make viewer` → http://127.0.0.1:8770.
- Статический snapshot для GitHub Pages: `make pages-export` → пишет `docs/report.json`.

## Устойчивые правила проекта (грабли, наступать не надо)

- **Неделя — с пятницы по пятницу.** Границы недели считаются Friday-to-Friday, чтобы совпадать с подписями недельных колонок в таблице. Не менять на пн-вс.
- **Запись в Google Sheets — всегда одним батчем** через `values.batchUpdate`. Одиночные вызовы на ячейку упираются в 429. Не писать в цикле по ячейке.
- **Поля могут быть пустыми.** Перед обращением к полям строки (например `readyNote`) проверять на null — пустое поле не должно ронять запись.
- **Topic ID (`T-001`, `T-002`, …) — якорь для write-back.** Это видимая передняя колонка в `Активные`, не скрытая служебная. Запись обратно опирается на неё, а не на позицию строки (защита от сдвига строк).
- **Только активные темы.** В collector-сценарии обрабатываются строки с lifecycle = active; paused игнорируются. Внутри активных сначала focus = yes.

## Интеграции

Adapters (Google Sheets, Telegram Bot/MTProto, SingularityApp, OpenAI Responses) включаются только при наличии env-переменных — см. `.env.example`. Проверка готовности: `make status` (`integration-status`). Доступы не предполагаются по умолчанию; без env адаптер просто выключен.

## Где что лежит

- Логика: `src/weekly_assistant/` — `cli.py`, `collector_server.py`, `viewer_server.py`, `integrations/`, `adapters/`.
- Интерфейс Евгения (GitHub Pages): `docs/index.html` + `docs/src/`. Читает публичный CSV Sheet через `gviz/tq` без API-ключа; fallback — `docs/report.json`.
- Маппинг колонок вкладки `Активные`: `docs/sheet_mapping.md`. Рабочий flow: `docs/workflows.md`.

## Стиль

Комментарии и текст в интерфейсе/отчётах — по-русски (это язык проекта и пользователя).
