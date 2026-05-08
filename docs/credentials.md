# Credentials Setup

Не вставляй токены в чат и не коммить `.env`. Файл `.env` уже исключен через `.gitignore`.

Подготовка:

```bash
cp .env.example .env
chmod 600 .env
```

После заполнения проверяй:

```bash
make status
```

## 1. OpenAI

Нужно для AI drafting.

Переменные:

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.4-mini
```

Как получить:

1. Открой https://platform.openai.com/.
2. Выбери нужный project.
3. Перейди в project settings -> API Keys.
4. Нажми `Create new secret key`.
5. Скопируй ключ сразу: повторно полностью он не показывается.
6. Вставь в `.env` как `OPENAI_API_KEY`.

Модель:

- для первого MVP-теста лучше `gpt-5.4-mini`;
- если качество важнее стоимости и скорости, поставь `gpt-5.5`.

Проверка после заполнения:

```bash
make status
```

Ожидаемо:

```text
openai_responses: configured
```

Источники:

- https://help.openai.com/en/articles/9186755-managing-your-work-in-the-api-platform-with-projects/
- https://developers.openai.com/api/docs/models

## 2. SingularityApp

Нужно для сценария `Отдать Володе`.

Переменные:

```dotenv
SINGULARITY_API_TOKEN=...
SINGULARITY_BASE_URL=https://api.singularity-app.com
SINGULARITY_PROJECT_ID=...
```

Как получить token:

1. Открой личный кабинет SingularityApp.
2. Перейди на экран `API Access`.
3. Нажми `Create Token`.
4. Назови токен, например `weekly-assistant`.
5. Выдай права минимум на:
   - tasks;
   - projects.
6. Создай токен.
7. Кликни по токену, чтобы скопировать его.
8. Вставь в `.env` как `SINGULARITY_API_TOKEN`.

Как получить `SINGULARITY_PROJECT_ID`:

1. Заполни `SINGULARITY_API_TOKEN`.
2. Запусти:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli singularity-list-projects
```

3. Найди строку с проектом `Отдать Володе`.
4. Скопируй `id=P-...` в `.env` как `SINGULARITY_PROJECT_ID`.
5. Проверь задачи проекта:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli singularity-list-tasks --project-id P-... --max-count 10
```

Важно:

- SingularityApp API не поддерживает webhooks/event streams.
- Поэтому первый сценарий должен быть polling/manual sync, а не live event ingestion.
- Если `https://api.singularity-app.com` не совпадет со Swagger base URL в твоем аккаунте, бери base URL из Swagger.
- Shared projects не возвращаются API search: документация говорит, что API возвращает только личные проекты аккаунта токена. Если проект виден в UI как shared, но API его не видит, нужен token владельца проекта или отдельный личный проект-приемник.
- MCP bundle `singularity-mcp-server-2.1.1.mcpb` не дает отдельного доступа: он использует тот же `SINGULARITY_API_TOKEN` и те же `/v2/...` endpoints. Для shared project результат будет тем же: `404` на project lookup или пустой список задач.
- Осторожно с MCP-логами: при ошибках server может печатать axios request config. Не запускай его в публичном логе с реальным token.

Источник:

- https://singularity-app.com/wiki/api/

## 3. Telegram Bot API

Нужно для helper-бота, команд и ручного сбора фактуры.

Переменные:

```dotenv
TELEGRAM_BOT_TOKEN=123456:...
```

Как получить:

1. Открой Telegram.
2. Найди `@BotFather`.
3. Отправь `/newbot`.
4. Задай display name.
5. Задай username, обычно он должен заканчиваться на `bot`.
6. BotFather пришлет token.
7. Вставь token в `.env` как `TELEGRAM_BOT_TOKEN`.

Проверка:

```bash
PYTHONPATH=src python3 -m weekly_assistant.cli telegram-get-me
```

Важно:

- Bot token дает полный контроль над ботом.
- Bot API не является полноценным способом читать историю старых чатов.
- Для группы бот должен быть добавлен в группу; privacy mode может ограничивать видимость сообщений.

Источники:

- https://core.telegram.org/bots
- https://core.telegram.org/bots/api

## 4. Telegram MTProto

Нужно только если реально хотим читать историю чатов как user-authorized client.

Переменные:

```dotenv
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_SESSION_NAME=weekly_assistant
```

Как получить:

1. Убедись, что Telegram account создан в официальном приложении.
2. Открой https://my.telegram.org/.
3. Войди по номеру телефона.
4. Перейди в `API development tools`.
5. Заполни форму приложения.
6. Скопируй `api_id` и `api_hash`.
7. Вставь в `.env`.

Рекомендуемые значения для формы:

```text
App title: Weekly Reports Tool
Short name: weeklyreportsnerealm
URL: https://example.com
Platform: Desktop
Description: Personal desktop tool for preparing weekly work reports from my own Telegram account.
```

Важно по форме:

- `Short name` должен быть строго alphanumeric: только латинские буквы и цифры, 5-32 символа.
- Не используй `_`, `-`, пробелы и кириллицу в `Short name`.
- Если появляется generic `ERROR`, сначала убери underscore из `Short name`, выбери `Desktop`, используй валидный `https://...` URL или реальный сайт.
- Если ошибка остается, попробуй другой уникальный `Short name`, например `weeklyreports2026`, и отключи VPN/proxy: `my.telegram.org` иногда отклоняет форму без подробной причины.

Если Telegram все равно не создает application:

- не блокируй MVP на MTProto;
- используй `Bot API` как forward-only inbox: пересылай релевантные сообщения в приватный чат/группу с ботом;
- для старой истории используй `Telegram Desktop -> Export chat history` и локальный парсинг export-файлов;
- для первого weekly-запуска допустим `assisted visual review`: открыть чаты, ограничить период и глазами собрать только факты для weekly;
- не используй чужие/public `api_id`/`api_hash` как рабочее решение: для нормального user-authorized клиента нужны свои credentials.

Важно:

- Это не bot token.
- Это user-client доступ, значит риски выше.
- Telegram предупреждает, что unofficial API clients мониторятся на abuse.
- Для MVP лучше начать с ручного paste из Telegram, а MTProto включать позже.

Источник:

- https://core.telegram.org/api/obtaining_api_id

## 5. Google Sheets

CSV-flow уже работает без Google credentials.

Нужно только для write-back или чтения private sheet через API.

Вариант A: быстрый временный access token

```dotenv
GOOGLE_SHEETS_SPREADSHEET_ID=14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs
GOOGLE_OAUTH_ACCESS_TOKEN=ya29...
```

Как получить:

1. Открой Google OAuth 2.0 Playground.
2. Выбери scope:

```text
https://www.googleapis.com/auth/spreadsheets
```

3. Authorize APIs.
4. Exchange authorization code for tokens.
5. Скопируй access token.
6. Вставь в `.env`.

Минус: access token короткоживущий.

Вариант B: service account JSON

```dotenv
GOOGLE_SHEETS_SPREADSHEET_ID=14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
```

Как получить:

1. Открой Google Cloud Console.
2. Создай или выбери project.
3. Enable Google Sheets API.
4. Создай service account.
5. Создай JSON key для service account.
6. Сохрани JSON локально вне репозитория.
7. Вставь абсолютный путь в `.env`.
8. Открой Google Sheet.
9. Нажми Share.
10. Добавь email service account с правами Editor.

Для локального кода дополнительно:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[google]'
make status
```

Важно:

- Google рекомендует short-lived credentials как более безопасный подход, но для локального MVP service account key проще.
- JSON key нельзя коммитить.

Источники:

- https://developers.google.com/workspace/sheets/api/quickstart/python
- https://cloud.google.com/iam/docs/keys-create-delete
- https://developers.google.com/identity/protocols/oauth2

## Минимальный порядок для нас

1. Для первого AI-теста:

```dotenv
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-mini
```

2. Для сценария `Отдать Володе`:

```dotenv
SINGULARITY_API_TOKEN=...
SINGULARITY_BASE_URL=https://api.singularity-app.com
SINGULARITY_PROJECT_ID=...
```

3. Для write-back в Google Sheet:

```dotenv
GOOGLE_SHEETS_SPREADSHEET_ID=14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
```

Telegram можно отложить: для MVP weekly-сборки он не блокирует первый запуск.
