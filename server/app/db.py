"""SQLite: messages with user_id. Server stamps created_at."""

from __future__ import annotations

import aiosqlite
from pathlib import Path
from typing import Any

from app.config import settings

DB_NAME = "nostos.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_user_created
    ON messages (user_id, created_at, id);
"""


def db_path() -> Path:
    root = Path(settings.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / DB_NAME


async def init_db() -> None:
    async with aiosqlite.connect(db_path()) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()


async def add_message(user_id: str, role: str, content: str) -> dict[str, Any]:
    async with aiosqlite.connect(db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        msg_id = cur.lastrowid
        await conn.commit()
        cur = await conn.execute(
            "SELECT id, user_id, role, content, created_at FROM messages WHERE id = ?",
            (msg_id,),
        )
        row = await cur.fetchone()
        return dict(row)


async def list_messages(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, user_id, role, content, created_at FROM messages "
            "WHERE user_id = ? ORDER BY id ASC LIMIT ?",
            (user_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def history_for_llm(user_id: str, limit: int = 40) -> list[dict[str, str]]:
    """Recent turns for the model. No system/memory injection in min-chat."""
    msgs = await list_messages(user_id, limit=limit)
    return [
        {"role": m["role"], "content": m["content"]}
        for m in msgs
        if m["role"] in ("user", "assistant")
    ]
