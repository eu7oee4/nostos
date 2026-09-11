"""SQLite: messages + wakes + push subscriptions. Server stamps created_at."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from app.config import settings

DB_NAME = "nostos.sqlite"

# wakes 的列清单只写一份：加一列时不用四处找 SELECT
WAKE_COLS = "id, user_id, wake_at, note, intent, status, source, created_at, fired_at"

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
    -- manual = 模型 wake_set / POST /wakes 定的；auto = 随机醒来挑的（#10）。
    -- 护栏只数 auto 那些，manual 不受日上限/最小间隔约束。
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    fired_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wakes_user_status_at
    ON wakes (user_id, status, wake_at);
CREATE INDEX IF NOT EXISTS idx_wakes_user_source_fired
    ON wakes (user_id, source, status, fired_at);

-- Web Push 订阅。endpoint 唯一：同一台设备重复订阅是覆盖，不是新增一行。
-- p256dh / auth 是浏览器给的加密材料，服务端只转发不解读。
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_ok_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_push_user
    ON push_subscriptions (user_id);
"""


def db_path() -> Path:
    root = Path(settings.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / DB_NAME


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    """补列 + 建表。老库（#10 之前建的）的 wakes 没有 source，这里补上。

    `CREATE TABLE IF NOT EXISTS` 对已存在的表一个字都不改，所以升级上来的库必须
    显式 ALTER。**补列要在 executescript 之前**：SCHEMA 里那个
    `idx_wakes_user_source_fired` 索引引用了 source，列还没有时整段脚本直接
    `OperationalError: no such column: source`，连表都建不完（实测）。

    新库走的是另一条：表还不存在 → table_info 空 → 跳过 ALTER，建表时就带着列。
    """
    cur = await conn.execute("PRAGMA table_info(wakes)")
    cols = {row[1] for row in await cur.fetchall()}
    if cols and "source" not in cols:
        await conn.execute(
            "ALTER TABLE wakes ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
        )
    await conn.executescript(SCHEMA)
    await conn.commit()


def _parse_ts(raw: str | None) -> datetime | None:
    """库里的戳（`...Z`）→ aware UTC datetime；坏值回 None。"""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def init_db() -> None:
    async with aiosqlite.connect(db_path()) as conn:
        await _ensure_schema(conn)


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Open DB once, ensure schema, close on exit (survives wipe while up)."""
    conn = await aiosqlite.connect(db_path())
    conn.row_factory = aiosqlite.Row
    try:
        await _ensure_schema(conn)
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
            f"SELECT {WAKE_COLS} "
            "FROM wakes WHERE id = ?",
            (wake_id,),
        )
        row = await cur.fetchone()
        return dict(row)


async def get_wake(wake_id: int) -> dict[str, Any] | None:
    async with _connect() as conn:
        cur = await conn.execute(
            f"SELECT {WAKE_COLS} "
            "FROM wakes WHERE id = ?",
            (wake_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_wakes(
    user_id: str,
    status: str | None = "pending",
    limit: int = 50,
    source: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    where = ["user_id = ?"]
    args: list[Any] = [user_id]
    if status:
        where.append("status = ?")
        args.append(status)
    if source:
        where.append("source = ?")
        args.append(source)
    # 查 pending 是「接下来会发生什么」，按时间正序；查全部是翻历史，最近的在前
    order = "wake_at ASC" if status == "pending" else "id DESC"
    args.append(limit)
    async with _connect() as conn:
        cur = await conn.execute(
            f"SELECT {WAKE_COLS} FROM wakes WHERE {' AND '.join(where)} "
            f"ORDER BY {order} LIMIT ?",
            tuple(args),
        )
        return [dict(r) for r in await cur.fetchall()]


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


async def count_pending_wakes(
    user_id: str | None = None,
    source: str | None = None,
) -> int:
    uid = user_id or settings.user_id
    async with _connect() as conn:
        if source:
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM wakes "
                "WHERE user_id = ? AND status = 'pending' AND source = ?",
                (uid, source),
            )
        else:
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM wakes "
                "WHERE user_id = ? AND status = 'pending'",
                (uid,),
            )
        row = await cur.fetchone()
        return int(row["n"] if row else 0)


# --- 随机醒来的护栏要问的三件事（#10）--------------------------------------
#
# 三个都只数 `source = 'auto'`：日上限 / 最小间隔管的是他自己起意来找你的次数，
# 不该被「你让他 9 点叫你起床」那种 manual 挤掉额度。


async def list_pending_auto_wakes(user_id: str) -> list[dict[str, Any]]:
    """待开火的随机醒来。正常只有 0 或 1 条（重挑前先把旧的作废）。"""
    async with _connect() as conn:
        cur = await conn.execute(
            f"SELECT {WAKE_COLS} "
            "FROM wakes WHERE user_id = ? AND status = 'pending' AND source = 'auto' "
            "ORDER BY wake_at ASC",
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def last_auto_fire_at(user_id: str) -> datetime | None:
    """上一次随机醒来真的开火是什么时候（最小间隔用）。"""
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
    user_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> int:
    """[start, end) 内开火过几次随机醒来（日上限用）。

    比的是字符串：库里的戳是 `2026-09-10T06:03:12.345Z`，边界给到秒
    （`2026-09-10T00:00:00`）——同为零填充 ISO，字典序即时序，前缀短一截也不影响。
    """
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


async def last_user_message_at(user_id: str) -> datetime | None:
    """用户最后一次说话的时间（「刚聊过就别随机来」用）。"""
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT created_at FROM messages WHERE user_id = ? AND role = 'user' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return _parse_ts(row["created_at"] if row else None)


# --- push subscriptions ---------------------------------------------------


async def upsert_push_subscription(
    user_id: str,
    endpoint: str,
    p256dh: str,
    auth: str,
) -> dict[str, Any]:
    """同一个 endpoint 再订阅就覆盖——浏览器换 key 时 endpoint 常常不变。"""
    async with _connect() as conn:
        await conn.execute(
            """
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id = excluded.user_id,
                p256dh = excluded.p256dh,
                auth = excluded.auth
            """,
            (user_id, endpoint, p256dh, auth),
        )
        await conn.commit()
        cur = await conn.execute(
            "SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
        row = await cur.fetchone()
        return dict(row) if row else {}


async def list_push_subscriptions(user_id: str | None = None) -> list[dict[str, Any]]:
    uid = user_id or settings.user_id
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT * FROM push_subscriptions WHERE user_id = ? ORDER BY id",
            (uid,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def delete_push_subscription(endpoint: str) -> bool:
    """推送被 endpoint 拒收（404/410）时调用——订阅过期了，留着只会每次都失败。"""
    async with _connect() as conn:
        cur = await conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
        await conn.commit()
        return cur.rowcount > 0


async def mark_push_ok(endpoint: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE push_subscriptions "
            "SET last_ok_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE endpoint = ?",
            (endpoint,),
        )
        await conn.commit()


async def count_push_subscriptions(user_id: str | None = None) -> int:
    uid = user_id or settings.user_id
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id = ?", (uid,)
        )
        row = await cur.fetchone()
        return int(row["n"] if row else 0)
