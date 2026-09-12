"""消息状态：用户句 pending → done / failed，重发复用同一行，重启扫 pending。"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import chat_loop, db
from app.llm import LLMError
from app.main import app

UID = "local"


async def _rows() -> list[dict]:
    return await db.list_messages(UID, limit=50)


# --- db 层 -------------------------------------------------------------------


@pytest.mark.anyio
async def test_begin_user_turn_is_pending_and_invisible_to_model(data_dir):
    await db.init_db()
    row = await db.begin_user_turn(UID, "在吗")
    assert row["role"] == "user" and row["status"] == "pending" and row["reason"] is None

    # 前端要看得见，模型看不见
    assert [m["id"] for m in await _rows()] == [row["id"]]
    assert await db.list_recent_turns(UID) == []


@pytest.mark.anyio
async def test_complete_turn_tx_marks_done_and_writes_reply_once(data_dir):
    await db.init_db()
    user = await db.begin_user_turn(UID, "在吗")

    reply = await db.complete_turn_tx(user["id"], UID, "在的")
    assert reply and reply["role"] == "assistant" and reply["status"] == "done"

    rows = await _rows()
    assert [(r["role"], r["status"]) for r in rows] == [("user", "done"), ("assistant", "done")]

    # 再来一次：user 已经不是 pending，一个字都不写
    assert await db.complete_turn_tx(user["id"], UID, "又在") is None
    assert len(await _rows()) == 2

    turns = await db.list_recent_turns(UID)
    assert [t["content"] for t in turns] == ["在吗", "在的"]


@pytest.mark.anyio
async def test_fail_turn_keeps_reason_and_reopen_only_for_latest(data_dir):
    await db.init_db()
    first = await db.begin_user_turn(UID, "第一句")
    assert await db.fail_turn(first["id"], "llm_502") is True
    assert await db.fail_turn(first["id"], "again") is False  # 只对 pending 生效

    got = (await _rows())[0]
    assert got["status"] == "failed" and got["reason"] == "llm_502"
    assert await db.list_recent_turns(UID) == []  # 失败句模型看不见

    # 重发：failed → pending，reason 清掉，同一个 id
    reopened = await db.reopen_turn(first["id"], UID)
    assert reopened["ok"] is True
    assert reopened["message"]["id"] == first["id"]
    assert reopened["message"]["status"] == "pending"
    assert reopened["message"]["reason"] is None

    # 不是 failed 的不能重开
    assert (await db.reopen_turn(first["id"], UID))["detail"] == "not_failed"
    assert (await db.reopen_turn(999, UID))["detail"] == "not_found"

    # 后面已经有新 user 句 → 拒绝：回复会排到新那轮之后
    await db.fail_turn(first["id"], "llm_502")
    await db.begin_user_turn(UID, "第二句")
    assert (await db.reopen_turn(first["id"], UID))["detail"] == "not_latest"
    assert (await _rows())[0]["status"] == "failed"


@pytest.mark.anyio
async def test_sweep_pending_turns_on_startup(data_dir):
    await db.init_db()
    a = await db.begin_user_turn(UID, "a")
    await db.complete_turn_tx(a["id"], UID, "ok")
    b = await db.begin_user_turn(UID, "b")  # 进程在这里挂了

    # 再启动一次 = 扫
    await db.init_db()
    by_id = {r["id"]: r for r in await _rows()}
    assert by_id[a["id"]]["status"] == "done"
    assert by_id[b["id"]]["status"] == "failed" and by_id[b["id"]]["reason"] == "restart"

    assert await db.sweep_pending_turns() == 0


# --- 迁移 ---------------------------------------------------------------------

OLD_MESSAGES_SCHEMA = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_messages_user_created ON messages (user_id, created_at, id);
INSERT INTO messages (user_id, role, content) VALUES ('local', 'user', '老的');
INSERT INTO messages (user_id, role, content) VALUES ('local', 'assistant', '老的回复');
"""


@pytest.mark.anyio
async def test_migrates_messages_without_status_column(data_dir):
    path = db.db_path()
    conn = sqlite3.connect(path)
    conn.executescript(OLD_MESSAGES_SCHEMA)
    conn.close()

    await db.init_db()

    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    assert {"status", "reason"} <= cols
    assert conn.execute("SELECT status, reason FROM messages").fetchall() == [
        ("done", None),
        ("done", None),
    ]
    idx = {r[1] for r in conn.execute("PRAGMA index_list(messages)")}
    assert "idx_messages_user_status" in idx
    conn.close()

    # 老行照常进历史、再启动一次幂等
    assert [t["content"] for t in await db.list_recent_turns(UID)] == ["老的", "老的回复"]
    await db.init_db()


# --- chat_loop 层 -------------------------------------------------------------


@pytest.fixture
def quiet_wake(monkeypatch):
    async def _noop(uid):
        return None

    monkeypatch.setattr(chat_loop, "ensure_auto_wake", _noop)


def _reply(text: str):
    async def _fake(messages, tools=None, tool_choice="auto"):
        _fake.seen.append(messages)
        return {"role": "assistant", "content": text}

    _fake.seen = []
    return _fake


def _boom(status: int):
    async def _fake(messages, tools=None, tool_choice="auto"):
        raise LLMError(status, "down")

    return _fake


@pytest.mark.anyio
async def test_run_chat_success_marks_both_done_and_no_duplicate_in_prompt(
    data_dir, monkeypatch, quiet_wake
):
    await db.init_db()
    fake = _reply("你好呀")
    monkeypatch.setattr(chat_loop, "chat_completion", fake)

    out = await chat_loop.run_chat("你好")
    assert out["message"]["content"] == "你好呀"

    rows = await _rows()
    assert [(r["role"], r["status"]) for r in rows] == [("user", "done"), ("assistant", "done")]

    # 当前句只由 Trigger 带，历史里不再出现一遍
    sent = fake.seen[0]
    user_lines = [m for m in sent if m["role"] == "user" and "你好" in m["content"]]
    assert len(user_lines) == 1


@pytest.mark.anyio
async def test_llm_failure_marks_failed_then_retry_reuses_row(
    data_dir, monkeypatch, quiet_wake
):
    await db.init_db()
    monkeypatch.setattr(chat_loop, "chat_completion", _boom(502))

    with pytest.raises(chat_loop.TurnFailed) as ei:
        await chat_loop.run_chat("在吗")
    failed = ei.value
    assert failed.reason == "llm_502"
    assert isinstance(failed.cause, LLMError)

    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["id"] == failed.user_message_id
    assert rows[0]["status"] == "failed" and rows[0]["reason"] == "llm_502"
    assert await db.list_recent_turns(UID) == []

    # 模型活过来了 → 重发：同一行 done，回复紧跟其后，历史里 user 句只有一条
    fake = _reply("在的")
    monkeypatch.setattr(chat_loop, "chat_completion", fake)
    out = await chat_loop.retry_chat(failed.user_message_id)
    assert out["message"]["content"] == "在的"

    rows = await _rows()
    assert [(r["id"], r["role"], r["status"]) for r in rows] == [
        (failed.user_message_id, "user", "done"),
        (rows[1]["id"], "assistant", "done"),
    ]
    assert [t["content"] for t in await db.list_recent_turns(UID)] == ["在吗", "在的"]
    # 重发那次拼装里，历史为空、当前句只在 Trigger 里
    assert sum(1 for m in fake.seen[0] if m["role"] == "user") == 1


@pytest.mark.anyio
async def test_retry_refuses_when_not_retryable(data_dir, monkeypatch, quiet_wake):
    await db.init_db()
    monkeypatch.setattr(chat_loop, "chat_completion", _boom(500))
    with pytest.raises(chat_loop.TurnFailed) as ei:
        await chat_loop.run_chat("第一句")
    first_id = ei.value.user_message_id

    monkeypatch.setattr(chat_loop, "chat_completion", _reply("好"))
    await chat_loop.run_chat("第二句")

    with pytest.raises(chat_loop.RetryRefused) as ei2:
        await chat_loop.retry_chat(first_id)
    assert ei2.value.detail == "not_latest"

    with pytest.raises(chat_loop.RetryRefused) as ei3:
        await chat_loop.retry_chat(424242)
    assert ei3.value.detail == "not_found"


@pytest.mark.anyio
async def test_non_llm_exception_is_recorded_as_error(data_dir, monkeypatch, quiet_wake):
    await db.init_db()

    async def _kaboom(messages, tools=None, tool_choice="auto"):
        raise RuntimeError("bug")

    monkeypatch.setattr(chat_loop, "chat_completion", _kaboom)
    with pytest.raises(chat_loop.TurnFailed) as ei:
        await chat_loop.run_chat("在吗")
    assert ei.value.reason == "error"
    assert (await _rows())[0]["reason"] == "error"


@pytest.mark.anyio
async def test_concurrent_sends_are_serialized_and_both_done(data_dir, monkeypatch, quiet_wake):
    await db.init_db()
    monkeypatch.setattr(chat_loop, "chat_completion", _reply("嗯"))
    await asyncio.gather(chat_loop.run_chat("一"), chat_loop.run_chat("二"))
    assert [(r["content"], r["status"]) for r in await _rows()] == [
        ("一", "done"), ("嗯", "done"), ("二", "done"), ("嗯", "done"),
    ]


# --- 路由层 -------------------------------------------------------------------


@pytest.fixture
def client(data_dir, quiet_wake):
    with TestClient(app) as c:
        yield c


def test_chat_failure_json_carries_message_id_and_retry_works(client, monkeypatch):
    monkeypatch.setattr(chat_loop, "chat_completion", _boom(401))
    r = client.post("/chat", json={"content": "在吗"})
    assert r.status_code == 503  # key 坏了是我们的问题
    body = r.json()
    assert body["message_status"] == "failed" and body["reason"] == "llm_401"
    mid = body["message_id"]

    msgs = client.get("/messages").json()["messages"]
    assert [(m["id"], m["status"], m["reason"]) for m in msgs] == [(mid, "failed", "llm_401")]

    monkeypatch.setattr(chat_loop, "chat_completion", _reply("在的"))
    r = client.post(f"/chat/{mid}/retry")
    assert r.status_code == 200 and r.json()["message"]["content"] == "在的"

    msgs = client.get("/messages").json()["messages"]
    assert [(m["role"], m["status"]) for m in msgs] == [("user", "done"), ("assistant", "done")]
    assert msgs[0]["id"] == mid

    # 已经 done 的不能再重发；不存在的 404
    assert client.post(f"/chat/{mid}/retry").status_code == 409
    assert client.post("/chat/9999/retry").status_code == 404


def test_retry_refused_when_newer_message_exists(client, monkeypatch):
    monkeypatch.setattr(chat_loop, "chat_completion", _boom(502))
    first = client.post("/chat", json={"content": "第一句"})
    assert first.status_code == 502
    mid = first.json()["message_id"]

    monkeypatch.setattr(chat_loop, "chat_completion", _reply("好"))
    assert client.post("/chat", json={"content": "第二句"}).status_code == 200

    r = client.post(f"/chat/{mid}/retry")
    assert r.status_code == 409 and "新消息" in r.json()["detail"]
