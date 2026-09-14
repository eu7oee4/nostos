"""会话段与重铸生命周期（PLAN §4.3、Notion「记忆系统设计对照」⑨，2026-09-14 定稿）。

要解决的两件事：原来是硬编码的最近 40 行窗口，每轮前缀都在变，缓存吃不到；
对话滚出窗口就断了连续性。段内纯追加吃缓存，重铸时靠 episode 接上。

**重铸只有一种形状**：新段 = 固定前缀 + 上一份 episode + 最近 N 轮对话原文。
episode 和那 N 轮会重叠，重叠是故意的：episode 给连续性，原文给具体语境和兜底。

**三条规则**：
1. 重铸只在两个时刻发生：硬闸轮末；或者用户回来时缓存已死。
2. 重铸前 episode 必须新鲜：距当前 ≤ N 轮。不新鲜就先提炼。N 和尾巴的 N 是同一个数。
3. 闲置时只做一件事：段过了软线且 episode 不新鲜就提炼，否则不动。

状态树：

    段内纯追加
    ├─ 硬闸轮末            → 提炼（缓存读）→ 重铸           after_turn()
    ├─ 闲置超阈值 且 过软线 → episode 新鲜？是：不动 / 否：提炼，段不关   idle_check()
    └─ 用户回来                                                   before_turn()
         ├─ 距上次调用 < TTL → 续原段
         └─ 距上次调用 ≥ TTL → 有新鲜 episode：重铸 / 没有：续段，付一次全价

提炼 = 原模型、原上下文：wake 那条管线，前缀不动，尾巴挂一条〔提炼〕触发句，
`tools=None`。触发句和产物**不进 messages 表**。产物落 episodes 表 + 一份
`data/episodes/<user>/seg-<id>.md`（角色日记，可看不可改，按段 id 覆盖）。
**不产记忆条目**：条目只从对话中的 memory_write_item / _feel 来。

这里所有入口都假定**调用方已持有 turn 锁**（chat_loop / scheduler 那边拿）。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app import db, llm
from app.config import settings
from app.context.assemble import Trigger, build_messages
from app.llm import LLMError, chat_completion

log = logging.getLogger("nostos.segment")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def episodes_dir(user_id: str | None = None) -> Path:
    uid = user_id or settings.user_id
    path = Path(settings.data_dir) / "episodes" / uid
    path.mkdir(parents=True, exist_ok=True)
    return path


async def context(user_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """拼装要的两样：当前段 + 本段历史行。聊天和 wake 都从这儿拿，前缀才逐字节一样。"""
    seg = await db.ensure_segment(user_id)
    rows = await db.list_segment_turns(user_id, int(seg["tail_from_msg_id"]))
    return seg, rows


async def episode_is_fresh(user_id: str, episode: dict[str, Any] | None) -> bool:
    if not episode:
        return False
    since = await db.turns_since(user_id, int(episode["covers_to_msg_id"]))
    return since <= settings.segment_tail_turns


async def cache_is_dead(user_id: str, now: datetime | None = None) -> bool:
    """距上一次任何 LLM 调用 ≥ TTL。从没调过 = 没什么可死的，算活。"""
    last = await db.last_llm_at(user_id)
    if last is None:
        return False
    at = now or _utc_now()
    return at - last >= timedelta(minutes=settings.segment_cache_ttl_minutes)


def _write_diary(user_id: str, segment_id: int, episode: dict[str, Any]) -> None:
    """角色日记：同一段重复提炼按段 id 覆盖。失败只记日志，不影响主流程。"""
    try:
        path = episodes_dir(user_id) / f"seg-{segment_id}.md"
        head = (
            f"<!-- episode {episode['id']} · segment {segment_id} · "
            f"{episode['trigger']} · {episode['created_at']} -->\n"
        )
        path.write_text(head + episode["content"].rstrip() + "\n", encoding="utf-8")
    except OSError:
        log.warning("episode diary write failed for segment %s", segment_id, exc_info=True)


async def distill(
    user_id: str, seg: dict[str, Any], trigger: str
) -> dict[str, Any] | None:
    """把本段提炼成一份 episode（stored）。模型挂了 / 空产物回 None，**记 WARNING**。"""
    rows = await db.list_segment_turns(user_id, int(seg["tail_from_msg_id"]))
    if not rows:
        log.info("distill skipped: segment %s has no turns", seg["id"])
        return None
    messages = build_messages(
        user_id=user_id,
        history_rows=rows,
        recall="",
        trigger=Trigger(kind="distill"),
        episode=seg.get("episode_text"),
    )
    started = time.monotonic()
    try:
        msg = await chat_completion(messages, tools=None)
    except LLMError as e:
        log.warning("distill failed for segment %s (%s): %s", seg["id"], trigger, e)
        return None
    ms = int((time.monotonic() - started) * 1000)
    text = (msg.get("content") or "").strip()
    if not text:
        log.warning("distill for segment %s came back empty", seg["id"])
        return None
    limit = settings.episode_max_chars
    if len(text) > limit:
        log.warning("episode for segment %s is %s chars, truncating to %s", seg["id"], len(text), limit)
        text = text[:limit].rstrip() + "…"

    usage = llm.last_usage or {}
    ep = await db.create_episode(
        user_id,
        segment_id=int(seg["id"]),
        covers_to_msg_id=int(rows[-1]["id"]),
        trigger=trigger,
        content=text,
        prompt_tokens=usage.get("prompt_tokens"),
        cache_hit_tokens=usage.get("prompt_cache_hit_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        ms=ms,
    )
    _write_diary(user_id, int(seg["id"]), ep)
    # Notion ⑦：每次提炼一行。cache_hit 接近 prompt 才说明「提炼永远是缓存读」成立；
    # 连续不命中 = 闲置阈值定大了，或者 DeepSeek 的缓存比想的短。
    log.info(
        "episode distilled id=%s segment=%s trigger=%s covers_to=%s turns=%s chars=%s "
        "prompt_tokens=%s cache_hit_tokens=%s completion_tokens=%s ms=%s",
        ep["id"],
        seg["id"],
        trigger,
        ep["covers_to_msg_id"],
        sum(1 for r in rows if r["role"] == "assistant"),
        len(text),
        ep["prompt_tokens"],
        ep["cache_hit_tokens"],
        ep["completion_tokens"],
        ms,
    )
    return ep


async def recast(
    user_id: str, old: dict[str, Any], episode: dict[str, Any], reason: str
) -> dict[str, Any]:
    """开新段：固定前缀 + 这份 episode + 最近 N 轮原文。episode 标 used。"""
    tail_from = await db.tail_from_for(user_id, settings.segment_tail_turns)
    new = await db.open_segment(
        user_id,
        tail_from_msg_id=tail_from,
        reason=reason,
        episode_id=int(episode["id"]),
        episode_text=episode["content"],
    )
    await db.mark_episode_used(int(episode["id"]))
    log.info(
        "segment recast reason=%s old=%s new=%s tail_from=%s episode=%s",
        reason,
        old["id"],
        new["id"],
        tail_from,
        episode["id"],
    )
    return new


async def before_turn(user_id: str, now: datetime | None = None) -> dict[str, Any]:
    """用户回来（每轮聊天开头）：缓存已死且有新鲜的存着的 episode → 重铸；否则续段。

    「没有新鲜的」那个分支付一次全价——只会是没过软线的短段（过了软线闲置时就提炼了），
    前缀小，不用补救。
    """
    seg = await db.ensure_segment(user_id)
    if not await cache_is_dead(user_id, now):
        return seg
    stored = await db.latest_episode(user_id, statuses=("stored",))
    if stored and await episode_is_fresh(user_id, stored):
        return await recast(user_id, seg, stored, "dead_cache")
    log.info("segment %s continues with cold cache (no fresh stored episode)", seg["id"])
    return seg


async def after_turn(user_id: str) -> dict[str, Any] | None:
    """轮末硬闸：段过了硬线 → 提炼 → 重铸。回新段；没动回 None。

    提炼失败不重铸——没有新鲜 episode 就重铸等于把这段忘掉。段越过硬线继续长，
    下一轮末再试。留痕：WARNING。
    """
    seg = await db.current_segment(user_id)
    if not seg:
        return None
    chars = await db.segment_chars(user_id, int(seg["tail_from_msg_id"]))
    if chars < settings.segment_hard_chars:
        return None
    log.info("hard gate: segment %s at %s chars (limit %s)", seg["id"], chars, settings.segment_hard_chars)
    ep = await distill(user_id, seg, "hard")
    if ep is None:
        log.warning("hard gate: distill failed, segment %s stays open past the limit", seg["id"])
        return None
    return await recast(user_id, seg, ep, "hard")


async def idle_check(user_id: str, now: datetime | None = None) -> str:
    """闲置到点：段过了软线且 episode 不新鲜才提炼；段不关，episode 只存着。

    回一个短 slug 给日志 / 测试：not_idle（中间又有调用，调度器会重挂）、
    under_soft、fresh、distilled、distill_failed。
    """
    at = now or _utc_now()
    last = await db.last_llm_at(user_id)
    if last is not None and at - last < timedelta(minutes=settings.segment_idle_minutes):
        return "not_idle"
    seg = await db.ensure_segment(user_id)
    chars = await db.segment_chars(user_id, int(seg["tail_from_msg_id"]))
    if chars < settings.segment_soft_chars:
        return "under_soft"
    latest = await db.latest_episode(user_id)
    if await episode_is_fresh(user_id, latest):
        return "fresh"
    ep = await distill(user_id, seg, "idle")
    return "distilled" if ep else "distill_failed"


async def status(user_id: str) -> dict[str, Any]:
    """`/stats` 用：当前段多大、episode 新不新鲜、利用率。"""
    seg = await db.current_segment(user_id)
    if not seg:
        return {"segment": None, "episodes": await db.episode_stats(user_id)}
    chars = await db.segment_chars(user_id, int(seg["tail_from_msg_id"]))
    rows = await db.list_segment_turns(user_id, int(seg["tail_from_msg_id"]))
    latest = await db.latest_episode(user_id)
    return {
        "segment": {
            "id": seg["id"],
            "reason": seg["reason"],
            "since": seg["created_at"],
            "turns": sum(1 for r in rows if r["role"] == "assistant"),
            "chars": chars,
            "soft_chars": settings.segment_soft_chars,
            "hard_chars": settings.segment_hard_chars,
            "has_episode_block": bool(seg.get("episode_text")),
        },
        "latest_episode": (
            {
                "id": latest["id"],
                "status": latest["status"],
                "trigger": latest["trigger"],
                "fresh": await episode_is_fresh(user_id, latest),
                "turns_since": await db.turns_since(user_id, int(latest["covers_to_msg_id"])),
            }
            if latest
            else None
        ),
        "episodes": await db.episode_stats(user_id),
    }
