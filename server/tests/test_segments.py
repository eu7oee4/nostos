"""会话段与重铸（docs/SEGMENTS.md，Notion ⑨ 的状态树）。

三条规则的可执行版：重铸只在硬闸轮末 / 回来时缓存已死；重铸前 episode 必须新鲜；
闲置只提炼不关段。谁把它改成「每轮重写历史」或者「没有新鲜 episode 也重铸」，这里先红。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import chat_loop, db, segments
from app.config import settings
from app.context.assemble import DISTILL_LINE, EPISODE_HEADER, Trigger, build_messages
from app.llm import LLMError
from app.main import app
from app.schedule import scheduler

UID = "local"


async def _turn(user: str, reply: str) -> tuple[int, int]:
    row = await db.begin_user_turn(UID, user)
    a = await db.complete_turn_tx(int(row["id"]), UID, reply)
    assert a is not None
    return int(row["id"]), int(a["id"])


async def _turns(n: int, size: int = 4) -> list[tuple[int, int]]:
    return [await _turn(f"问{i:02d}" + "。" * size, f"答{i:02d}" + "。" * size) for i in range(n)]


def _fake(text: str, *, raise_status: int | None = None):
    async def _call(messages, tools=None, tool_choice="auto"):
        _call.seen.append({"messages": messages, "tools": tools})
        if raise_status:
            raise LLMError(raise_status, "down")
        return {"role": "assistant", "content": text}

    _call.seen = []
    return _call


@pytest.fixture
def quiet(monkeypatch):
    async def _noop(uid):
        return None

    monkeypatch.setattr(chat_loop, "ensure_auto_wake", _noop)
    monkeypatch.setattr(chat_loop, "arm_idle_check", lambda uid=None: False)


# --- 段的边界 ---------------------------------------------------------------


@pytest.mark.anyio
async def test_first_segment_on_old_db_keeps_the_40_row_window(data_dir):
    await db.init_db()
    pairs = await _turns(25)  # 50 行
    seg = await db.ensure_segment(UID)
    assert seg["reason"] == "init" and seg["episode_text"] is None
    # 最近 40 行 = 从第 6 轮（下标 5）的 user 句起
    assert seg["tail_from_msg_id"] == pairs[5][0]
    rows = await db.list_segment_turns(UID, seg["tail_from_msg_id"])
    assert rows == await db.list_recent_turns(UID, limit=40)
    # 再叫一次不会开第二段
    assert (await db.ensure_segment(UID))["id"] == seg["id"]


@pytest.mark.anyio
async def test_fresh_db_segment_starts_at_zero(data_dir):
    await db.init_db()
    seg = await db.ensure_segment(UID)
    assert seg["tail_from_msg_id"] == 0
    await _turns(2)
    assert len(await db.list_segment_turns(UID, 0)) == 4


@pytest.mark.anyio
async def test_tail_counts_a_wake_as_one_turn(data_dir):
    await db.init_db()
    pairs = await _turns(12)
    wake = await db.add_message(UID, "assistant", "嘿，我来看你啦。")
    # 最近 10 轮 = wake + 第 4..12 轮（9 轮）；从第 4 轮（下标 3）的 user 句起
    assert await db.tail_from_for(UID, 10) == pairs[3][0]
    # 不足 N 轮：全部
    assert await db.tail_from_for(UID, 40) == 0
    # 最老那轮本身是 wake 时，从 wake 那行起
    await _turns(9)
    assert await db.tail_from_for(UID, 10) == int(wake["id"])
    # turns_since 也数 wake
    assert await db.turns_since(UID, pairs[-1][1]) == 10


# --- 提炼 -------------------------------------------------------------------


@pytest.mark.anyio
async def test_distill_stores_episode_writes_diary_and_supersedes(data_dir, monkeypatch):
    await db.init_db()
    pairs = await _turns(3)
    seg = await db.open_segment(UID, tail_from_msg_id=0, reason="init", episode_text="旧回忆")
    fake = _fake("主题：测试\n摘要：聊了三轮\n正文：…")
    monkeypatch.setattr(segments, "chat_completion", fake)

    ep = await segments.distill(UID, seg, "idle")
    assert ep and ep["status"] == "stored" and ep["trigger"] == "idle"
    assert ep["covers_to_msg_id"] == pairs[-1][1]
    assert ep["ms"] is not None

    # 原模型、原上下文：前缀里有本段的 episode 块 + 全部历史，尾巴挂〔提炼〕，不给工具
    sent = fake.seen[0]
    assert sent["tools"] is None
    systems = [m["content"] for m in sent["messages"] if m["role"] == "system"]
    assert any(s == f"{EPISODE_HEADER}\n旧回忆" for s in systems)
    assert not any("浮现" in s for s in systems)  # 提炼不带召回
    last = sent["messages"][-1]
    assert last["role"] == "user" and last["content"].split("\n")[-1].startswith("〔停一下")
    assert "把它并进来" in DISTILL_LINE
    assert sum(1 for m in sent["messages"] if m["role"] == "user") == 4  # 3 轮 + 触发

    diary = data_dir / "episodes" / UID / f"seg-{seg['id']}.md"
    assert diary.is_file() and "主题：测试" in diary.read_text(encoding="utf-8")

    # 同段再提炼：老的 superseded，日记按段 id 覆盖
    fake2 = _fake("主题：第二版")
    monkeypatch.setattr(segments, "chat_completion", fake2)
    ep2 = await segments.distill(UID, seg, "idle")
    assert (await db.get_episode(ep["id"]))["status"] == "superseded"
    assert ep2["status"] == "stored"
    assert "第二版" in diary.read_text(encoding="utf-8")
    assert (await db.episode_stats(UID)) == {
        "distilled": 2, "used": 0, "superseded": 1, "stored": 1, "by_trigger": {"idle": 2},
    }


@pytest.mark.anyio
async def test_distill_truncates_and_handles_failure(data_dir, monkeypatch):
    await db.init_db()
    await _turns(1)
    seg = await db.ensure_segment(UID)
    monkeypatch.setattr(settings, "episode_max_chars", 20)
    monkeypatch.setattr(segments, "chat_completion", _fake("x" * 100))
    ep = await segments.distill(UID, seg, "hard")
    assert ep and len(ep["content"]) == 21 and ep["content"].endswith("…")

    monkeypatch.setattr(segments, "chat_completion", _fake("", raise_status=502))
    assert await segments.distill(UID, seg, "hard") is None
    monkeypatch.setattr(segments, "chat_completion", _fake("   "))
    assert await segments.distill(UID, seg, "hard") is None


# --- 硬闸 -------------------------------------------------------------------


@pytest.mark.anyio
async def test_hard_gate_distills_then_recasts_with_tail(data_dir, monkeypatch, quiet):
    await db.init_db()
    monkeypatch.setattr(settings, "segment_hard_chars", 60)
    monkeypatch.setattr(settings, "segment_tail_turns", 2)
    chat = _fake("答" + "。" * 9)
    monkeypatch.setattr(chat_loop, "chat_completion", chat)
    distill = _fake("主题：硬闸\n摘要：聊满了")
    monkeypatch.setattr(segments, "chat_completion", distill)

    for i in range(3):  # 每轮 20 字，第 3 轮末 60 ≥ 60 触硬闸
        await chat_loop.run_chat("问" + "。" * 9)
    seg = await db.current_segment(UID)
    assert seg["reason"] == "hard" and seg["episode_text"] == "主题：硬闸\n摘要：聊满了"
    assert len(distill.seen) == 1
    ep = await db.latest_episode(UID)
    assert ep["status"] == "used" and ep["trigger"] == "hard" and ep["used_at"]
    # 尾巴 = 最近 2 轮，从第 2 轮的 user 句起
    rows = await db.list_messages(UID)
    assert seg["tail_from_msg_id"] == rows[2]["id"]

    # 下一轮：前缀 = 固定 + episode 块 + 那 2 轮原文，第 1 轮不在了
    await chat_loop.run_chat("再问")
    sent = chat.seen[-1]["messages"]
    systems = [m["content"] for m in sent if m["role"] == "system"]
    assert f"{EPISODE_HEADER}\n主题：硬闸\n摘要：聊满了" in systems
    users = [m for m in sent if m["role"] == "user"]
    assert len(users) == 3  # 2 轮尾巴 + 当前句
    # episode 块在 prefs 位置之后、对话之前
    idx_ep = systems.index(f"{EPISODE_HEADER}\n主题：硬闸\n摘要：聊满了")
    first_user = next(i for i, m in enumerate(sent) if m["role"] == "user")
    assert sent[idx_ep] == {"role": "system", "content": systems[idx_ep]} and idx_ep < first_user


@pytest.mark.anyio
async def test_hard_gate_without_episode_keeps_segment_open(data_dir, monkeypatch, quiet):
    await db.init_db()
    monkeypatch.setattr(settings, "segment_hard_chars", 10)
    monkeypatch.setattr(chat_loop, "chat_completion", _fake("回复"))
    monkeypatch.setattr(segments, "chat_completion", _fake("", raise_status=502))
    before = await db.ensure_segment(UID)
    await chat_loop.run_chat("这句够长了吧")
    after = await db.current_segment(UID)
    assert after["id"] == before["id"]  # 没有新鲜 episode 就不重铸，段越线继续
    assert await db.latest_episode(UID) is None


# --- 闲置 -------------------------------------------------------------------


@pytest.mark.anyio
async def test_idle_check_branches(data_dir, monkeypatch):
    await db.init_db()
    monkeypatch.setattr(settings, "segment_soft_chars", 30)
    monkeypatch.setattr(settings, "segment_tail_turns", 2)
    monkeypatch.setattr(settings, "segment_idle_minutes", 15)
    later = datetime.now(timezone.utc) + timedelta(minutes=20)
    fake = _fake("主题：闲置")
    monkeypatch.setattr(segments, "chat_completion", fake)

    await _turns(1, size=2)
    assert await segments.idle_check(UID, now=datetime.now(timezone.utc)) == "not_idle"
    assert await segments.idle_check(UID, now=later) == "under_soft"

    await _turns(3, size=4)
    seg_before = await db.current_segment(UID)
    assert await segments.idle_check(UID, now=later) == "distilled"
    assert fake.seen and (await db.latest_episode(UID))["status"] == "stored"
    assert (await db.current_segment(UID))["id"] == seg_before["id"]  # 段不关

    # 新鲜：不动
    assert await segments.idle_check(UID, now=later) == "fresh"
    assert len(fake.seen) == 1

    # 过了 2 轮就不新鲜了：再提炼，老的被覆盖
    await _turns(3, size=4)
    assert await segments.idle_check(UID, now=later + timedelta(hours=1)) == "distilled"
    stats = await db.episode_stats(UID)
    assert stats["distilled"] == 2 and stats["superseded"] == 1 and stats["stored"] == 1

    monkeypatch.setattr(segments, "chat_completion", _fake("", raise_status=500))
    await _turns(3, size=4)
    assert await segments.idle_check(UID, now=later + timedelta(hours=2)) == "distill_failed"


# --- 用户回来 ---------------------------------------------------------------


@pytest.mark.anyio
async def test_before_turn_recasts_only_when_cache_dead_and_episode_fresh(data_dir, monkeypatch):
    await db.init_db()
    monkeypatch.setattr(settings, "segment_cache_ttl_minutes", 20)
    monkeypatch.setattr(settings, "segment_tail_turns", 2)
    now = datetime.now(timezone.utc)
    dead = now + timedelta(minutes=25)

    # 没调过模型：什么都不做
    seg0 = await segments.before_turn(UID, now=dead)
    assert seg0["reason"] == "init"

    await _turns(3)
    seg = await db.ensure_segment(UID)
    # 缓存还活着：续段
    assert (await segments.before_turn(UID, now=now))["id"] == seg["id"]
    # 死了但没有存着的 episode：续段，付一次全价
    assert (await segments.before_turn(UID, now=dead))["id"] == seg["id"]

    monkeypatch.setattr(segments, "chat_completion", _fake("主题：回来"))
    ep = await segments.distill(UID, seg, "idle")
    # 提炼本身也是一次调用，缓存计时从它起算
    assert not await segments.cache_is_dead(UID, now=now)
    assert await segments.cache_is_dead(UID, now=dead)

    new = await segments.before_turn(UID, now=dead)
    assert new["id"] != seg["id"] and new["reason"] == "dead_cache"
    assert new["episode_id"] == ep["id"] and new["episode_text"] == "主题：回来"
    assert (await db.get_episode(ep["id"]))["status"] == "used"
    # 用过的不会再用：再来一次缓存死了也只能续段
    assert (await segments.before_turn(UID, now=dead + timedelta(hours=1)))["id"] == new["id"]

    # 存着的但不新鲜：不重铸
    ep2 = await segments.distill(UID, new, "idle")
    await _turns(3)
    assert not await segments.episode_is_fresh(UID, ep2)
    assert (await segments.before_turn(UID, now=dead + timedelta(hours=2)))["id"] == new["id"]


# --- 拼装 / wake / 路由 ------------------------------------------------------


def test_episode_block_sits_after_prefs_before_dialog(data_dir):
    (data_dir / "prefs.json").write_text(
        '{"style": [{"id": "brevity", "text": "说短点"}], "wake": {}}', encoding="utf-8"
    )
    history = [{"role": "user", "content": "在吗", "created_at": "2026-09-14T03:00:00Z"}]
    msgs = build_messages(
        user_id=UID, history_rows=history, recall="", trigger=Trigger(kind="user", text="hi"),
        episode="主题：上一段",
    )
    contents = [m["content"] for m in msgs]
    i = contents.index(f"{EPISODE_HEADER}\n主题：上一段")
    assert msgs[i - 1]["content"].startswith("【纠正过的说话方式】")
    assert msgs[i + 1]["role"] == "user"
    # 空的不留占位
    msgs = build_messages(
        user_id=UID, history_rows=history, recall="", trigger=Trigger(kind="user", text="hi"),
        episode="  ",
    )
    assert not any(EPISODE_HEADER in m["content"] for m in msgs)


@pytest.mark.anyio
async def test_wake_line_uses_segment_context(data_dir, monkeypatch):
    await db.init_db()
    await _turns(3)
    await db.open_segment(
        UID, tail_from_msg_id=(await db.tail_from_for(UID, 1)), reason="hard", episode_text="主题：段"
    )
    fake = _fake("嗨")
    monkeypatch.setattr(scheduler, "chat_completion", fake)
    assert await scheduler._generate_wake_line(UID, "check_in") == "嗨"
    sent = fake.seen[0]["messages"]
    assert any(m["content"] == f"{EPISODE_HEADER}\n主题：段" for m in sent if m["role"] == "system")
    assert sum(1 for m in sent if m["role"] == "user") == 2  # 尾巴 1 轮 + wake 触发


def test_arm_idle_check_is_noop_without_scheduler():
    assert scheduler._scheduler is None
    assert scheduler.arm_idle_check(UID) is False


@pytest.fixture
def client(data_dir):
    with TestClient(app) as c:
        yield c


def test_stats_and_episode_routes(client, monkeypatch):
    monkeypatch.setattr(chat_loop, "chat_completion", _fake("好"))
    assert client.post("/chat", json={"content": "在吗"}).status_code == 200
    seg = client.get("/stats").json()["segments"]
    assert seg["segment"]["turns"] == 1 and seg["latest_episode"] is None
    assert seg["episodes"]["distilled"] == 0
    assert client.get("/episodes").json()["episodes"] == []
    assert client.get("/episodes/1").status_code == 404
    # 没有删改入口：角色的日记用户可看不可改
    assert client.delete("/episodes/1").status_code == 405
