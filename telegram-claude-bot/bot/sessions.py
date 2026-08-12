"""Привязка веток переписки в Telegram к сессиям Claude.

Каждое «корневое» сообщение пользователя открывает новую сессию агента.
Все сообщения ветки (ответы бота и реплаи пользователя) маппятся на корень,
поэтому reply на любое сообщение ветки продолжает ту же сессию.
"""

import sqlite3
from contextlib import closing

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    root_msg_id INTEGER PRIMARY KEY,
    session_id  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS msg_map (
    msg_id      INTEGER PRIMARY KEY,
    root_msg_id INTEGER NOT NULL REFERENCES chats(root_msg_id)
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def create_chat(root_msg_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT OR IGNORE INTO chats (root_msg_id) VALUES (?)", (root_msg_id,)
        )
        conn.execute(
            "INSERT OR REPLACE INTO msg_map (msg_id, root_msg_id) VALUES (?, ?)",
            (root_msg_id, root_msg_id),
        )


def set_session(root_msg_id: int, session_id: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE chats SET session_id = ? WHERE root_msg_id = ?",
            (session_id, root_msg_id),
        )


def link_message(msg_id: int, root_msg_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO msg_map (msg_id, root_msg_id) VALUES (?, ?)",
            (msg_id, root_msg_id),
        )


def find_root(msg_id: int) -> int | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT root_msg_id FROM msg_map WHERE msg_id = ?", (msg_id,)
        ).fetchone()
        return row[0] if row else None


def get_session(root_msg_id: int) -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT session_id FROM chats WHERE root_msg_id = ?", (root_msg_id,)
        ).fetchone()
        return row[0] if row else None
