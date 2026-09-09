"""In-process wake scheduler (APScheduler). Our API says wake, never job."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import db, push
from app.config import settings
from app.context.assemble import Trigger, build_messages
from app.context.scrub import scrub_reply
from app.llm import LLMError, chat_completion
from app.memory import recall_text

log = logging.getLogger("nostos.wake")

_scheduler: AsyncIOScheduler | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_wake_at(
    *,
    wake_at: str | None = None,
    delay_seconds: int | float | None = None,
) -> datetime:
    if delay_seconds is not None:
        sec = float(delay_seconds)
        if sec < 0:
            raise ValueError("delay_seconds must be >= 0")
        from datetime import timedelta

        return _utc_now() + timedelta(seconds=sec)
    if not wake_at or not str(wake_at).strip():
        raise ValueError("provide wake_at (ISO UTC) or delay_seconds")
    raw = str(wake_at).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _wake_key(wake_id: int) -> str:
    return f"wake-{wake_id}"


def _arm_in_scheduler(wake_id: int, when: datetime) -> None:
    """Register a one-shot wake with APScheduler (library API uses add_job)."""
    if _scheduler is None:
        raise RuntimeError("wake scheduler not started")
    # APScheduler's method name is fixed; do not rename our domain to "job".
    _scheduler.add_job(
        fire_wake,
        trigger="date",
        run_date=when,
        args=[wake_id],
        id=_wake_key(wake_id),
        replace_existing=True,
        misfire_grace_time=300,
    )


def disarm_wake(wake_id: int) -> None:
    if _scheduler is None:
        return
    key = _wake_key(wake_id)
    try:
        _scheduler.remove_job(key)  # library API
    except Exception:  # noqa: BLE001
        pass


async def fire_wake(wake_id: int) -> None:
    row = await db.get_wake(wake_id)
    if not row:
        return
    if row["status"] != "pending":
        return

    uid = row["user_id"]
    note = (row.get("note") or "").strip()
    intent = (row.get("intent") or "check_in").strip()

    if note:
        text = note
    else:
        text = await _generate_wake_line(uid, intent)

    await db.add_message(uid, "assistant", text)
    await db.mark_wake_fired(wake_id)
    log.info("wake fired id=%s user=%s", wake_id, uid)

    # 出站：推到用户手机上。站内那条 assistant 才是真相来源，推送是附加动作——
    # `push.notify` 自己吞异常，推失败不影响这条 wake 已经落库、已经 fired。
    # 不推的话「它主动来」（PLAN §1 假设 2）只有打开网页才看得见，等于没测。
    await push.notify(uid, text)


async def _generate_wake_line(user_id: str, intent: str) -> str:
    """到点自己开口说的那句：走**同一条**拼装管线，触发换成 7b。

    原来这里是第二条装配——自己拼一段 system + 旧口径的「【当前记忆召回】」，
    没有 profile、没有 persona、没有对话历史。后果是同一个伙伴主动来找你时，
    人格和记忆口径跟聊天时对不上。DESIGN §2.3 点名的反条款：「不另写第二条装配
    （复制装配管线 = 屎山源）」。

    不给工具（tools=None）：主动消息不重新进入带工具的 agent loop，用架构堵死
    「对一条提醒采取行动」，不靠模型自觉（PLAN §13 从 Raven 抄的那条）。
    """
    turns = await db.list_recent_turns(user_id, limit=40)
    messages = build_messages(
        user_id=user_id,
        history_rows=turns,
        recall=recall_text(user_id),
        trigger=Trigger(kind="wake", intent=intent),
    )
    try:
        msg = await chat_completion(messages, tools=None)
        return scrub_reply((msg.get("content") or "").strip()) or "嘿，我来看你啦。"
    except LLMError:
        return "嘿，我来看你啦。"


async def schedule_wake(
    *,
    user_id: str | None = None,
    wake_at: str | None = None,
    delay_seconds: int | float | None = None,
    note: str | None = None,
    intent: str = "check_in",
) -> dict[str, Any]:
    if not settings.proactive_enabled:
        return {
            "ok": False,
            "detail": "proactive wakes are off — set PROACTIVE_ENABLED=true and restart",
        }
    if _scheduler is None or not _scheduler.running:
        return {"ok": False, "detail": "wake scheduler not running"}

    try:
        when = parse_wake_at(wake_at=wake_at, delay_seconds=delay_seconds)
    except ValueError as e:
        return {"ok": False, "detail": str(e)}

    uid = user_id or settings.user_id
    iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    note_clean = (note or "").strip() or None
    row = await db.create_wake(uid, iso, note=note_clean, intent=intent or "check_in")
    wake_id = int(row["id"])

    if when <= _utc_now():
        await fire_wake(wake_id)
        fresh = await db.get_wake(wake_id)
        return {"ok": True, "wake": fresh, "fired_immediately": True}

    _arm_in_scheduler(wake_id, when)
    return {"ok": True, "wake": row, "fired_immediately": False}


async def restore_pending_wakes() -> int:
    """Re-arm pending wakes after process start."""
    if not settings.proactive_enabled:
        return 0
    rows = await db.list_wakes(settings.user_id, status="pending", limit=200)
    armed = 0
    now = _utc_now()
    for row in rows:
        wake_id = int(row["id"])
        try:
            when = parse_wake_at(wake_at=row["wake_at"])
        except ValueError:
            continue
        if when <= now:
            await fire_wake(wake_id)
        else:
            _arm_in_scheduler(wake_id, when)
            armed += 1
    return armed


async def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    _scheduler = AsyncIOScheduler(timezone=timezone.utc)
    _scheduler.start()
    if settings.proactive_enabled:
        n = await restore_pending_wakes()
        log.info("wake scheduler started; restored_pending=%s", n)
    else:
        log.info("wake scheduler started; proactive disabled (no arming)")


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
