"""SQLite: messages + wakes. Server stamps created_at."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
    source TEXT NOT NULL DEFAULT 'manual',
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


async def _migrate(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA)
    cur = await conn.execute("PRAGMA table_info(wakes)")
    cols = {row[1] for row in await cur.fetchall()}
    if "source" not in cols:
        await conn.execute(
            "ALTER TABLE wakes ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
        )
    await conn.commit()


async def init_db() -> None:
    async with aiosqlite.connect(db_path()) as conn:
        await _migrate(conn)


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(db_path())
    conn.row_factory = aiosqlite.Row
    try:
        await _migrate(conn)
        yield conn
    finally:
        await conn.close()


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT id, user_id, role, content, created_at FROM messages "
            "WHERE user_id = ? ORDER BY id ASC LIMIT ?",
            (user_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def history_for_llm(user_id: str, limit: int = 40) -> list[dict[str, str]]:
    msgs = await list_messages(user_id, limit=limit)
    return [
        {"role": m["role"], "content": m["content"]}
        for m in msgs
        if m["role"] in ("user", "assistant")
    ]


async def last_user_message_at(user_id: str) -> datetime | None:
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT created_at FROM messages WHERE user_id = ? AND role = 'user' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return _parse_ts(row["created_at"] if row else None)


async def create_wake(
    user_id: str,
    wake_at: str,
    note: str | None = None,
    intent: str = "check_in",
    source: str = "manual",
) -> dict[str, Any]:
    src = source if source in ("manual", "auto") else "manual"
    async with _connect() as conn:
        cur = await conn.execute(
            "INSERT INTO wakes (user_id, wake_at, note, intent, status, source) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (user_id, wake_at, note, intent or "check_in", src),
        )
        wake_id = cur.lastrowid
        await conn.commit()
        cur = await conn.execute(
            "SELECT id, user_id, wake_at, note, intent, status, source, created_at, fired_at "
            "FROM wakes WHERE id = ?",
            (wake_id,),
        )
        row = await cur.fetchone()
        return dict(row)


async def get_wake(wake_id: int) -> dict[str, Any] | None:
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT id, user_id, wake_at, note, intent, status, source, created_at, fired_at "
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
                "SELECT id, user_id, wake_at, note, intent, status, source, created_at, fired_at "
                "FROM wakes WHERE user_id = ? AND status = ? "
                "ORDER BY wake_at ASC LIMIT ?",
                (user_id, status, limit),
            )
        else:
            cur = await conn.execute(
                "SELECT id, user_id, wake_at, note, intent, status, source, created_at, fired_at "
                "FROM wakes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def list_pending_auto_wakes(user_id: str) -> list[dict[str, Any]]:
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT id, user_id, wake_at, note, intent, status, source, created_at, fired_at "
            "FROM wakes WHERE user_id = ? AND status = 'pending' AND source = 'auto' "
            "ORDER BY wake_at ASC",
            (user_id,),
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


async def last_auto_fire_at(user_id: str) -> datetime | None:
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT fired_at FROM wakes WHERE user_id = ? AND status = 'fired' "
            "AND source = 'auto' AND fired_at IS NOT NULL "
            "ORDER BY fired_at DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return _parse_ts(row["fired_at"] if row else None)


async def count_auto_fires_between(
    user_id: str, start_utc: datetime, end_utc: datetime
) -> int:
    start_s = start_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    end_s = end_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM wakes WHERE user_id = ? AND status = 'fired' "
            "AND source = 'auto' AND fired_at IS NOT NULL "
            "AND fired_at >= ? AND fired_at < ?",
            (user_id, start_s, end_s),
        )
        row = await cur.fetchone()
        return int(row["n"] if row else 0)
