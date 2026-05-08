# Weekly Collector

Локальный browser-инструмент для еженедельного сбора фактуры без работы в VS Code.

## Запуск

```bash
make collector WEEK_START=2026-05-01 WEEK_END=2026-05-07
```

По умолчанию запуск продолжает существующую session. Если нужно полностью перечитать Google Sheet и начать заново:

```bash
make collector WEEK_START=2026-05-01 WEEK_END=2026-05-07 COLLECTOR_REFRESH=--refresh
```

Открой:

```text
http://127.0.0.1:8765
```

## Что делает

- скачивает текущую weekly-вкладку из Google Sheet;
- показывает active-темы;
- подтягивает подсказки из Singularity по `config/singularity_projects.csv`;
- дает быстрые статусы: результат, процесс, вопрос, не трогал;
- принимает сырой текст по теме;
- умеет разложить bulk dump по темам через OpenAI;
- копирует контекст для ChatGPT Project;
- импортирует финальный JSON из ChatGPT Project;
- генерирует AI draft для строки;
- сохраняет session локально;
- экспортирует candidate CSV, questions, sync и summary;
- может записать измененные строки обратно в Google Sheet.

## Где лежит session

```text
exports/collector_sessions/<week-start>_<week-end>/
```

Основные файлы:

```text
session.json
source.csv
candidate.csv
questions.txt
sync.txt
summary.txt
milestones.txt
chatgpt_context.json
```

## Рабочий сценарий

1. Запусти `make collector`.
2. Если работаешь через ChatGPT UI: нажми `Copy ChatGPT context`.
3. Вставь контекст в ChatGPT Project с prompt из `docs/chatgpt_weekly_project_prompt.md`.
4. Пройди интервью в ChatGPT и скопируй итоговый JSON.
5. Вставь JSON в поле `ChatGPT JSON` и нажми `Import ChatGPT JSON`.
6. Проверь строки в collector.
7. Нажми `Export`.
8. Если результат нормальный, нажми `Write Sheet`.

Альтернативно можно работать внутри collector: вставить общий сырой weekly dump в `Сырой dump`, нажать `Разложить по темам`, пройти темы и нажимать `AI draft`.

## Правила AI draft

- `Прошлая неделя` в правой панели — только справка для человека.
- AI draft не использует прошлую неделю как факт нового отчета.
- AI draft не использует импортированные из таблицы `Факты этой недели`, пока ты не изменил их в текущей session.
- `Вопрос к Евгению` появляется только если ты явно написал его в сыром вводе или руками заполнил поле вопроса.
- Если новой фактуры нет, строка должна честно уйти в `no_movement`.

## Вехи

- Внутри session поддерживается несколько вех через `milestones[]`.
- В UI поле `Все вехи` хранит одну веху на строку.
- При записи в Google Sheet все вехи собираются в одну строку через `;` в поле `Ближайшая веха`.
- `Дата вехи` получает дату первой вехи, то есть ближайшей.

## Правила безопасности

- `Write Sheet` пишет только измененные строки.
- Обычный запуск не перезатирает локальную session.
- `COLLECTOR_REFRESH=--refresh` перезагружает session из Google Sheet и может стереть локальные черновики, если они не записаны в Sheet.
- Запись идет в диапазон `H:S`, то есть поля weekly-сборки.
- Lifecycle/архивирование тем этим инструментом не меняются.
- Если формулировка сомнительная, сначала `Export`, потом ручной просмотр.
