# Integration Matrix

Проверено по официальным источникам 2026-05-06.

## Google Sheets

Статус: API подтвержден.

Что доступно:

- чтение и запись значений через `spreadsheets.values`;
- структурные изменения, форматирование, добавление листов и data validation через `spreadsheets.batchUpdate`.

MVP-решение:

- CSV остается fallback;
- Google Sheets adapter включается после настройки OAuth/service account;
- write-back делать через отдельный service, не смешивать с weekly engine.

Источник:

- https://developers.google.com/workspace/sheets/api/guides/values
- https://developers.google.com/sheets/api/guides/batchupdate

## Telegram

Статус: API подтвержден, но есть два разных сценария.

Bot API:

- подходит для команд бота, ручного ввода, новых сообщений боту/в группе, где бот присутствует;
- `getUpdates` и webhooks являются взаимоисключающими способами получения updates;
- update backlog хранится ограниченное время, поэтому Bot API не является надежным источником исторической выгрузки.

MTProto Telegram API:

- подходит для чтения истории чатов через user-authorized client;
- требует `api_id` и `api_hash`;
- метод `messages.getHistory` доступен только пользователям, не ботам.

MVP-решение:

- на старте оставить paste/manual Telegram ingestion;
- Bot API использовать только как вспомогательный интерфейс;
- MTProto adapter проектировать отдельно и включать только после осознанного решения по рискам пользовательской авторизации.

Fallback, если `my.telegram.org` не дает получить `api_id`/`api_hash`:

- `bot inbox`: создать приватный чат/группу `Weekly Inbox`, добавить туда бота и пересылать релевантные сообщения в течение недели; работает через Bot API, но не читает старую историю;
- `Telegram Desktop export`: разово экспортировать выбранные чаты в JSON/HTML и парсить локальные файлы; это bulk/manual export, но лучше, чем копировать сообщения по одному;
- `assisted visual review`: открыть Telegram Web/Desktop, ограничить список чатов и период, затем глазами собрать факты в structured notes; это не интеграция, а временный weekly-сбор без API;
- не использовать чтение локальных кэшей Telegram Desktop как MVP-интеграцию: формат нестабилен, доступы/шифрование зависят от клиента, риск приватности выше пользы.

Источники:

- https://core.telegram.org/bots/api
- https://core.telegram.org/api/obtaining_api_id
- https://core.telegram.org/method/messages.getHistory

## SingularityApp

Статус: API подтвержден.

Что доступно:

- token-based API access;
- CRUD для задач и проектов;
- `/v2/task` для списка задач и фильтрации;
- Bearer token в Authorization header;
- Swagger доступен после получения token.

Ограничения:

- API не поддерживает webhooks, event streams или subscriptions;
- recurring tasks через API не создаются;
- technical support не настраивает custom scenarios.
- shared projects не появляются в результатах поиска проектов через API; API возвращает только личные проекты аккаунта, под которым создан token.
- MCP server `singularity-mcp-server-2.1.1.mcpb` является оберткой над тем же REST API: использует `baseUrl`, `accessToken`, `/v2/project`, `/v2/task` и Bearer token; отдельного механизма доступа к shared projects в нем нет.

MVP-решение:

- использовать только для отдельного сценария `Отдать Володе`;
- polling/manual sync, не event-driven architecture;
- base URL и project id не хардкодить, держать в env.
- если `Отдать Володе` является shared project, нужен token владельца проекта или перенос/дублирование задач в личный проект, доступный API.
- не использовать MCP как обход прав доступа; он может быть полезен только как интерфейс к тем же доступным объектам API.

Источник:

- https://singularity-app.com/wiki/api/

## OpenAI

Статус: API подтвержден.

Что использовать:

- Responses API как основной LLM endpoint;
- Structured Outputs для weekly-row JSON, чтобы модель возвращала валидную схему, а не свободный текст.

MVP-решение:

- LLM provider должен быть optional;
- без `OPENAI_API_KEY` система работает в rule-based/export-only режиме;
- `OPENAI_MODEL` задается явно через env, без скрытого выбора модели в коде.

Источники:

- https://platform.openai.com/docs/api-reference/responses
- https://platform.openai.com/docs/guides/structured-outputs
