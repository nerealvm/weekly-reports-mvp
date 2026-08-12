"""Telegram-бот: каждое пересланное сообщение — новая сессия Claude-агента."""

import asyncio
import html
import logging
import time

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.filters import CommandStart
from aiogram.types import Message

from . import claude_runner, config, sessions, transcribe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

MAX_TG_LEN = 4000


def _origin_line(message: Message) -> str | None:
    origin = message.forward_origin
    if origin is None:
        return None
    kind = origin.type  # user / hidden_user / chat / channel
    if kind == "user":
        who = origin.sender_user.full_name
    elif kind == "hidden_user":
        who = origin.sender_user_name
    elif kind == "chat":
        who = f"чат «{origin.sender_chat.title}»"
    elif kind == "channel":
        who = f"канал «{origin.chat.title}»"
    else:
        who = "неизвестный источник"
    return f"Переслано из Telegram, автор/источник: {who}, дата оригинала: {origin.date:%Y-%m-%d %H:%M}."


async def _download(file_obj, suffix: str) -> str:
    """Скачивает файл Telegram в рабочую директорию агента, возвращает путь."""
    path = config.MEDIA_DIR / f"{int(time.time())}_{file_obj.file_unique_id}{suffix}"
    await bot.download(file_obj, destination=str(path))
    return str(path)


async def _build_prompt(message: Message) -> str:
    """Собирает промпт для агента из содержимого сообщения."""
    parts: list[str] = []

    origin = _origin_line(message)
    if origin:
        parts.append(origin)

    text = message.text or message.caption
    if text:
        parts.append(f"Текст сообщения:\n{text}")

    audio = message.voice or message.audio or message.video_note
    if audio:
        suffix = ".oga" if message.voice else ".mp4" if message.video_note else ".m4a"
        path = await _download(audio, suffix)
        try:
            transcript = await transcribe.transcribe(path)
        except Exception:
            log.exception("Ошибка транскрибации")
            transcript = ""
        if transcript:
            parts.append(f"Расшифровка голосового/аудио:\n{transcript}")
        else:
            parts.append(f"Голосовое сообщение сохранено в файл {path}, расшифровать не удалось.")

    if message.photo:
        path = await _download(message.photo[-1], ".jpg")
        parts.append(f"К сообщению приложено изображение, файл: {path}. Открой его (Read) и учти содержимое.")

    if message.document:
        name = message.document.file_name or "file.bin"
        suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ".bin"
        path = await _download(message.document, suffix)
        parts.append(f"К сообщению приложен документ «{name}», файл: {path}. Изучи его содержимое.")

    if message.video:
        path = await _download(message.video, ".mp4")
        parts.append(f"К сообщению приложено видео, файл: {path} (посмотреть его ты не можешь, но файл доступен).")

    if not parts:
        return ""

    if not message.reply_to_message and not text:
        parts.append("Отдельного поручения нет — разбери это сообщение по своей стандартной схеме.")

    return "\n\n".join(parts)


def _resolve_session(message: Message) -> tuple[int, str | None]:
    """Определяет корень ветки и session_id для продолжения диалога."""
    if message.reply_to_message:
        root = sessions.find_root(message.reply_to_message.message_id)
        if root is not None:
            sessions.link_message(message.message_id, root)
            return root, sessions.get_session(root)
    root = message.message_id
    sessions.create_chat(root)
    return root, None


async def _typing_forever(chat_id: int) -> None:
    while True:
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:
            pass
        await asyncio.sleep(5)


async def _reply_long(message: Message, text: str, root: int) -> None:
    for i in range(0, len(text), MAX_TG_LEN):
        chunk = text[i : i + MAX_TG_LEN]
        sent = await message.reply(chunk)
        sessions.link_message(sent.message_id, root)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.from_user.id not in config.ALLOWED_USER_IDS:
        await message.reply("Это личный бот, доступ ограничен.")
        return
    await message.reply(
        "Привет! Пересылай мне любые сообщения — текст, голосовые, картинки, "
        "документы. На каждое я запускаю новый чат с Claude и разбираю его. "
        "Чтобы продолжить разбор — просто ответь (reply) на моё сообщение."
    )


@dp.message(F.chat.type == "private")
async def handle_message(message: Message) -> None:
    if message.from_user.id not in config.ALLOWED_USER_IDS:
        return

    prompt = await _build_prompt(message)
    if not prompt:
        await message.reply("Не понял, что с этим делать: тут нет ни текста, ни медиа.")
        return

    root, session_id = _resolve_session(message)
    is_new = session_id is None

    placeholder = await message.reply("Новый чат, разбираю…" if is_new else "Продолжаю разбор…")
    sessions.link_message(placeholder.message_id, root)

    typing = asyncio.create_task(_typing_forever(message.chat.id))
    try:
        answer, new_session_id = await claude_runner.run_agent(prompt, session_id)
        if new_session_id:
            sessions.set_session(root, new_session_id)
    except asyncio.TimeoutError:
        answer = "Агент не уложился в отведённое время. Попробуй ещё раз или упрости запрос."
    except Exception as exc:
        log.exception("Ошибка агента")
        answer = f"Ошибка при запуске агента: {html.escape(str(exc)[:500])}"
    finally:
        typing.cancel()

    try:
        await placeholder.delete()
    except Exception:
        pass
    await _reply_long(message, answer, root)


async def main() -> None:
    log.info("Бот запущен, модель: %s", config.CLAUDE_MODEL)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
