# Architecture

## Принцип

MVP строится как нормализованное ядро + adapters.

Source of truth на первом этапе остается Google Sheet. Код не обязан писать в Google Sheet напрямую: CSV-flow является базовым и надежным fallback.

## Слои

`domain`

- `WeeklyRow`
- `WeeklySession`
- `IncomingItem`
- enums и JSON schemas

`adapters`

- CSV read/write;
- далее можно добавить XLSX adapter;
- Google Sheets adapter живет в `integrations`, потому что требует внешней авторизации.

`services`

- `weekly_engine`: create-week, carry-forward, computed fields;
- `movement_service`: real movement / no movement / unclear;
- `milestone_service`: перенос и извлечение даты;
- `sync_recommendation_service`: rule-based sync;
- `drafting_service`: rule-based fallback;
- `incoming_service`: входящие из ручного ввода / Telegram / Singularity;
- `exports`: questions, sync, summary.

`integrations`

- Google Sheets API;
- Telegram Bot API;
- Telegram MTProto boundary;
- SingularityApp API;
- OpenAI Responses API.

## Почему так

- weekly можно собрать без API и секретов;
- интеграции проверяются отдельно через `integration-status`;
- LLM не является source of truth;
- таблица остается главным интерфейсом редактирования;
- write-back можно добавить после стабилизации mapping.
