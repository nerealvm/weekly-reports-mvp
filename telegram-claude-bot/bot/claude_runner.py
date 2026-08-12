"""Запуск агент-сессий через Claude Agent SDK."""

import asyncio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

from . import config

SYSTEM_PROMPT = """\
Ты — личный ассистент, работающий через Telegram-бота. Пользователь пересылает
тебе сообщения из Telegram в любом виде: текст, расшифровки голосовых, картинки,
документы, ссылки.

Если к пересланному сообщению нет отдельного поручения — разбери его сам:
определи, что это (задача, идея, контакт, документ, счёт, статья, заметка),
вытащи суть и главные факты, и предложи конкретные следующие шаги. Если из
сообщения очевидна простая задача (что-то посчитать, найти, составить,
проверить) — сразу выполни её, не переспрашивая.

Если поручение есть — выполняй его.

У тебя есть рабочая директория с файлами: присланные медиа и документы лежат
там, открывай их инструментом Read. Результаты своей работы (файлы, черновики)
тоже сохраняй туда и указывай путь в ответе.

Отвечай на русском. Пиши для Telegram: обычный текст без markdown-заголовков
и таблиц, короткие абзацы, самое важное — в начале. Ответ по существу, без
воды."""


async def run_agent(prompt: str, session_id: str | None) -> tuple[str, str | None]:
    """Прогоняет один запрос через агента.

    Возвращает (текст ответа, session_id для продолжения диалога).
    """
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=config.CLAUDE_MODEL,
        cwd=str(config.WORKDIR),
        permission_mode="bypassPermissions",
        resume=session_id,
    )

    last_text = ""
    all_text: list[str] = []
    new_session_id = session_id

    async def _consume() -> None:
        nonlocal last_text, new_session_id
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                parts = [b.text for b in message.content if isinstance(b, TextBlock)]
                if parts:
                    last_text = "\n".join(parts)
                    all_text.append(last_text)
            elif isinstance(message, ResultMessage):
                new_session_id = message.session_id or new_session_id
                result = getattr(message, "result", None)
                if isinstance(result, str) and result.strip():
                    last_text = result

    await asyncio.wait_for(_consume(), timeout=config.CLAUDE_TIMEOUT_SEC)

    text = last_text.strip() or "\n\n".join(all_text).strip()
    return text or "Готово (агент не вернул текстового ответа).", new_session_id
