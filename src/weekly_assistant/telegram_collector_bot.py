"""Telegram bot that applies a weekly import JSON to the active Google Sheet.

The bot is a remote trigger for code that already runs on this machine, not a
conversational agent: the interview happens in the Claude app, the bot only
receives the resulting JSON and runs the tested import/write-back pipeline.
"""

import argparse
import json
import time
from pathlib import Path

from weekly_assistant.collector_server import CollectorConfig, CollectorStore, _load_chatgpt_payload
from weekly_assistant.config.settings import load_settings
from weekly_assistant.integrations.telegram_bot import TelegramBotAdapter
from weekly_assistant.services.active_sheet import ACTIVE_SHEET_NAME, week_label_for_date
from weekly_assistant.utils.http import JsonHttpClient
from weekly_assistant.utils.weeks import current_week_bounds

HELP_TEXT = (
    "Пришли JSON-файл недельного импорта — применю его к листу «Активные».\n\n"
    "/status — какая неделя и сколько активных тем\n"
    "/help — это сообщение"
)


def apply_import(settings, args, payload_text: str) -> str:
    week_start, week_end = current_week_bounds()
    store = CollectorStore(
        settings,
        CollectorConfig(
            spreadsheet_id=args.spreadsheet_id or settings.google_sheets_spreadsheet_id,
            gid=args.gid,
            sheet_name=args.sheet_name,
            week_start=week_start,
            week_end=week_end,
            session_dir=Path(args.session_dir),
            source_csv=None,
            # Always rebuild from the sheet: import maps by row_number and resolved
            # week columns, so a stale session would write to the wrong cells.
            refresh=True,
        ),
    )
    imported = store.import_chatgpt_json(payload_text)
    written = store.write_back()

    lines = [
        f"Неделя {week_label_for_date(week_end)} · лист «{written.get('target_sheet', args.sheet_name)}»",
        f"Импортировано тем: {imported.get('imported_count', 0)}",
        f"Записано строк: {written.get('updated_count', 0)}",
    ]
    created = written.get("created_week_columns") or []
    if created:
        lines.append(f"Создано колонок недели: {', '.join(map(str, created))}")
    unmatched = imported.get("unmatched") or []
    if unmatched:
        names = ", ".join(
            f"{item.get('topic_id') or '?'} {item.get('topic_title') or ''}".strip()
            for item in unmatched
        )
        lines.append(f"\nНе сматчились ({len(unmatched)}): {names}")
    return "\n".join(lines)


def describe_status(settings, args) -> str:
    week_start, week_end = current_week_bounds()
    return (
        f"Неделя {week_label_for_date(week_end)} ({week_start} — {week_end})\n"
        f"Лист: {args.sheet_name}\n"
        f"Жду JSON-файл недельного импорта."
    )


def extract_payload(adapter: TelegramBotAdapter, message: dict) -> str | None:
    document = message.get("document")
    if document:
        name = (document.get("file_name") or "").lower()
        if not name.endswith(".json"):
            raise ValueError(f"Нужен .json, пришёл «{document.get('file_name')}»")
        info = adapter.get_file(document["file_id"])
        file_path = info.get("result", {}).get("file_path")
        if not file_path:
            raise ValueError("Telegram не отдал путь к файлу")
        return adapter.download_file(file_path).decode("utf-8")
    text = (message.get("text") or "").strip()
    if text.startswith("{") or text.startswith("```"):
        return text
    return None


def handle_message(adapter: TelegramBotAdapter, settings, args, message: dict) -> None:
    chat_id = str(message.get("chat", {}).get("id", ""))
    sender_id = str(message.get("from", {}).get("id", ""))
    if sender_id != str(args.allowed_user_id):
        print(f"[bot] ignored message from user_id={sender_id}", flush=True)
        return

    text = (message.get("text") or "").strip()
    if text in {"/start", "/help"}:
        adapter.send_message(chat_id, HELP_TEXT)
        return
    if text == "/status":
        adapter.send_message(chat_id, describe_status(settings, args))
        return

    try:
        payload = extract_payload(adapter, message)
    except Exception as exc:
        adapter.send_message(chat_id, f"Не смог забрать файл: {exc}")
        return

    if payload is None:
        adapter.send_message(chat_id, HELP_TEXT)
        return

    try:
        _load_chatgpt_payload(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        adapter.send_message(chat_id, f"Это не валидный JSON: {exc}")
        return

    adapter.send_message(chat_id, "Принял, применяю…")

    try:
        adapter.send_message(chat_id, apply_import(settings, args, payload))
    except Exception as exc:
        print(f"[bot] import failed: {type(exc).__name__}: {exc}", flush=True)
        adapter.send_message(chat_id, f"Запись не прошла: {type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="weekly-collector-bot")
    parser.add_argument("--gid", default="0")
    parser.add_argument("--sheet-name", default=ACTIVE_SHEET_NAME)
    parser.add_argument("--spreadsheet-id", default="")
    parser.add_argument("--session-dir", default="/tmp/weekly-bot")
    parser.add_argument("--poll-timeout", type=int, default=50)
    args = parser.parse_args()

    settings = load_settings()
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set.")
        return 1
    # Fail closed: this process writes to a live business spreadsheet.
    if not settings.telegram_allowed_user_id:
        print("TELEGRAM_ALLOWED_USER_ID is not set. Refusing to start without a whitelist.")
        return 1
    args.allowed_user_id = settings.telegram_allowed_user_id

    # The HTTP read timeout must outlast the long poll, or every idle poll
    # aborts client-side and the bot listens in bursts instead of continuously.
    adapter = TelegramBotAdapter(settings, JsonHttpClient(timeout_seconds=args.poll_timeout + 15))
    me = adapter.get_me().get("result", {})
    print(f"[bot] listening as @{me.get('username')} for user_id={args.allowed_user_id}", flush=True)

    offset: int | None = None
    while True:
        try:
            updates = adapter.get_updates(offset=offset, timeout=args.poll_timeout)
        except Exception as exc:
            print(f"[bot] poll error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(5)
            continue
        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message") or update.get("edited_message")
            if message:
                try:
                    handle_message(adapter, settings, args, message)
                except Exception as exc:
                    print(f"[bot] handler error: {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
