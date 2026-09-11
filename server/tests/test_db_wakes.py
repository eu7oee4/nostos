"""wakes 表：开火原子、终态留痕、老库迁移、接受率。"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from app import db

# #10 时代的 wakes：CHECK 只认三态、没有 reason。迁移测试从这份起步。
OLD_SCHEMA = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE wakes (
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
CREATE INDEX idx_wakes_user_status_at ON wakes (user_id, status, wake_at);
CREATE INDEX idx_wakes_user_source_fired ON wakes (user_id, source, status, fired_at);
INSERT INTO wakes (user_id, wake_at, note, status) VALUES ('local', '2026-09-08T00:00:00Z', 'a', 'pending');
INSERT INTO wakes (user_id, wake_at, note, status) VALUES ('local', '2026-09-08T00:00:00Z', 'b', 'cancelled');
"""


@pytest.mark.anyio
async def test_fire_wake_tx_writes_once(data_dir):
    await db.init_db()
    row = await db.create_wake("local", "2026-09-08T00:00:00Z", note="嘿")

    msg = await db.fire_wake_tx(row["id"], "local", "嘿")
    assert msg and msg["role"] == "assistant" and msg["content"] == "嘿"

    # 第二次：状态已不是 pending，什么都不写
    assert await db.fire_wake_tx(row["id"], "local", "嘿") is None
    assert len(await db.list_messages("local")) == 1

    fresh = await db.get_wake(row["id"])
    assert fresh["status"] == "fired" and fresh["fired_at"]


@pytest.mark.anyio
async def test_terminal_states_keep_reason(data_dir):
    await db.init_db()
    a = await db.create_wake("local", "2026-09-08T00:00:00Z", source="auto")
    b = await db.create_wake("local", "2026-09-08T00:00:00Z")

    assert await db.mark_wake_skipped(a["id"], "missed")
    assert await db.mark_wake_cancelled(b["id"], "random_disabled")
    # 已经关掉的不能再改
    assert not await db.mark_wake_skipped(a["id"], "again")
    assert not await db.mark_wake_cancelled(a["id"])

    rows = {r["id"]: r for r in await db.list_wakes("local", status=None)}
    assert (rows[a["id"]]["status"], rows[a["id"]]["reason"]) == ("skipped", "missed")
    assert (rows[b["id"]]["status"], rows[b["id"]]["reason"]) == ("cancelled", "random_disabled")

    with pytest.raises(ValueError):
        await db._close_wake(a["id"], "fired", None)


@pytest.mark.anyio
async def test_migrates_three_state_wakes_table(data_dir):
    conn = sqlite3.connect(db.db_path())
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()

    await db.init_db()

    # 老数据还在，新状态写得进去
    rows = await db.list_wakes("local", status=None)
    assert {r["note"] for r in rows} == {"a", "b"}
    assert all("reason" in r for r in rows)
    pending = next(r for r in rows if r["note"] == "a")
    assert await db.mark_wake_skipped(pending["id"], "missed")

    conn = sqlite3.connect(db.db_path())
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'wakes'").fetchone()[0]
    indexes = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'wakes'")
    }
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert "'skipped'" in sql
    assert {"idx_wakes_user_status_at", "idx_wakes_user_source_fired"} <= indexes
    assert "wakes_old" not in tables

    # 再跑一次 init 是幂等的
    await db.init_db()


@pytest.mark.anyio
async def test_wake_stats_acceptance(data_dir):
    await db.init_db()
    w1 = await db.create_wake("local", "2026-09-08T00:00:00Z", note="x", source="auto")
    assert await db.fire_wake_tx(w1["id"], "local", "x")
    await asyncio.sleep(0.01)
    await db.add_message("local", "user", "在的")

    w2 = await db.create_wake("local", "2026-09-08T00:00:00Z", note="y")
    assert await db.fire_wake_tx(w2["id"], "local", "y")

    w3 = await db.create_wake("local", "2026-09-08T00:00:00Z", source="auto")
    await db.mark_wake_skipped(w3["id"], "quiet_hours")

    s = await db.wake_stats("local", days=7, reply_hours=6)
    assert s["fired"] == 2 and s["replied"] == 1 and s["acceptance"] == 0.5
    assert s["by_source"] == {"auto": {"fired": 1, "replied": 1}, "manual": {"fired": 1, "replied": 0}}
    assert s["closed"] == {"skipped:quiet_hours": 1}


@pytest.mark.anyio
async def test_wake_stats_empty(data_dir):
    await db.init_db()
    s = await db.wake_stats("local")
    assert s["fired"] == 0 and s["acceptance"] is None
