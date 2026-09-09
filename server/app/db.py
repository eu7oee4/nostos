"""SQLite: messages + wakes. Server stamps created_at."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

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

CREATE TABLE IF NOT EXISTS wakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    wake_at TEXT NOT NULL,
    note TEXT,
    intent TEXT NOT NULL DEFAULT 'check_in',
    status TEXT NOT NULL CHECK (status IN ('pending', 'fired', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    fired_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wakes_user_status_at
    ON wakes (user_id, status, wake_at);
"""


def db_path() -> Path:
    root = Path(settings.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / DB_NAME


async def init_db() -> None:
    async with aiosqlite.connect(db_path()) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Open DB once, ensure schema, close on exit (survives wipe while up)."""
    conn = await aiosqlite.connect(db_path())
    conn.row_factory = aiosqlite.Row
    try:
        await conn.executescript(SCHEMA)
        await conn.commit()
        yield conn
    finally:
        await conn.close()


async def add_message(user_id: str, role: str, content: str) -> dict[str, Any]:
    async with _connect() as conn:
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
    """最近 limit 条，按时间正序返回。

    取最近要 `ORDER BY id DESC LIMIT ?` 再翻转——写成 ASC LIMIT 取到的是**最旧**
    的 limit 条：历史一过 limit，模型看到的窗口就冻在最早那段，新对话永远进不去
    prompt，前端也不再显示新消息。
    """
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT id, user_id, role, content, created_at FROM messages "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def list_recent_turns(user_id: str, limit: int = 40) -> list[dict[str, Any]]:
    """Recent user/assistant rows with created_at for prompt assembly."""
    msgs = await list_messages(user_id, limit=limit)
    return [
        {
            "id": m["id"],
            "role": m["role"],
            "content": m["content"],
            "created_at": m["created_at"],
        }
        for m in msgs
        if m["role"] in ("user", "assistant")
    ]


async def history_for_llm(user_id: str, limit: int = 40) -> list[dict[str, str]]:
    """Recent turns for the model (role/content only; prefer list_recent_turns)."""
    turns = await list_recent_turns(user_id, limit=limit)
    return [{"role": t["role"], "content": t["content"]} for t in turns]


async def create_wake(
    user_id: str,
    wake_at: str,
    note: str | None = None,
    intent: str = "check_in",
) -> dict[str, Any]:
    async with _connect() as conn:
        cur = await conn.execute(
            "INSERT INTO wakes (user_id, wake_at, note, intent, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (user_id, wake_at, note, intent or "check_in"),
        )
        wake_id = cur.lastrowid
        await conn.commit()
        cur = await conn.execute(
            "SELECT id, user_id, wake_at, note, intent, status, created_at, fired_at "
            "FROM wakes WHERE id = ?",
            (wake_id,),
        )
        row = await cur.fetchone()
        return dict(row)


async def get_wake(wake_id: int) -> dict[str, Any] | None:
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT id, user_id, wake_at, note, intent, status, created_at, fired_at "
            "FROM wakes WHERE id = ?",
            (wake_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_wakes(
    user_id: str,
    status: str | None = "pending",
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    async with _connect() as conn:
        if status:
            cur = await conn.execute(
                "SELECT id, user_id, wake_at, note, intent, status, created_at, fired_at "
                "FROM wakes WHERE user_id = ? AND status = ? "
                "ORDER BY wake_at ASC LIMIT ?",
                (user_id, status, limit),
            )
        else:
            cur = await conn.execute(
                "SELECT id, user_id, wake_at, note, intent, status, created_at, fired_at "
                "FROM wakes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def mark_wake_fired(wake_id: int) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE wakes SET status = 'fired', "
            "fired_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (wake_id,),
        )
        await conn.commit()


async def mark_wake_cancelled(wake_id: int) -> bool:
    async with _connect() as conn:
        cur = await conn.execute(
            "UPDATE wakes SET status = 'cancelled' "
            "WHERE id = ? AND status = 'pending'",
            (wake_id,),
        )
        await conn.commit()
        return cur.rowcount > 0


async def count_pending_wakes(user_id: str | None = None) -> int:
    uid = user_id or settings.user_id
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM wakes WHERE user_id = ? AND status = 'pending'",
            (uid,),
        )
        row = await cur.fetchone()
        return int(row["n"] if row else 0)
