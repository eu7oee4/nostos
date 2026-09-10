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
from app.prefs import load_wake
from app.schedule.policy import (
    can_fire_now,
    in_quiet_hours,
    next_auto_wake_at,
    random_on,
    slot_allowed,
)

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
    source = (row.get("source") or "manual").strip()
    wake_prefs = load_wake()

    # 随机醒来到点还要**再过一遍护栏**：武装的那一刻合规，不代表现在还合规
    # （中间用户可能刚聊过、或已经被别的随机醒来吃掉了今天的额度）。
    # 挡下来就作废重挑，**一个 token 都不烧**——护栏挡掉的那条根本不调模型。
    if source == "auto":
        ok, reason = await can_fire_now(uid, wake_prefs)
        if not ok:
            await db.mark_wake_cancelled(wake_id)
            log.info("auto wake id=%s dropped before generating: %s", wake_id, reason)
            await ensure_auto_wake(uid)
            return

    note = (row.get("note") or "").strip()
    intent = (row.get("intent") or "check_in").strip()

    if note:
        text = note
    else:
        text = await _generate_wake_line(uid, intent)

    await db.add_message(uid, "assistant", text)
    await db.mark_wake_fired(wake_id)
    log.info("wake fired id=%s user=%s source=%s", wake_id, uid, source)

    # 出站：推到用户手机上。站内那条 assistant 才是真相来源，推送是附加动作——
    # `push.notify` 自己吞异常，推失败不影响这条 wake 已经落库、已经 fired。
    # 不推的话「它主动来」（PLAN §1 假设 2）只有打开网页才看得见，等于没测。
    #
    # 安静时段里**静默投递**：他照样醒、照样生成、照样落进聊天记录，只是不顶到
    # 锁屏上。护栏管的是「几点可以吵你」，不是「几点不许存在」——第二天打开就看见
    # 他半夜说过什么，这是伙伴该有的样子；凌晨三点震你一下不是。
    # （auto 那条根本走不到这儿：can_fire_now 里安静时段就已经挡掉了。）
    if in_quiet_hours(_utc_now(), wake_prefs):
        log.info("wake id=%s in quiet hours: delivered in-app, no push", wake_id)
    else:
        await push.notify(uid, text)

    if source == "auto":
        await ensure_auto_wake(uid)


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
    source: str = "manual",
) -> dict[str, Any]:
    """预约一次 wake。

    `source="manual"`（模型 `wake_set` / `POST /wakes`）**不过护栏**：时刻由模型
    定，APScheduler 只负责到点执行（DESIGN §6）。安静时段对它唯一的影响是到点
    那条不推送，见 `fire_wake`。`source="auto"` 只由 `ensure_auto_wake` 用。
    """
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
    row = await db.create_wake(
        uid,
        iso,
        note=note_clean,
        intent=intent or "check_in",
        source=source if source in ("manual", "auto") else "manual",
    )
    wake_id = int(row["id"])

    if when <= _utc_now():
        await fire_wake(wake_id)
        fresh = await db.get_wake(wake_id)
        return {"ok": True, "wake": fresh, "fired_immediately": True}

    _arm_in_scheduler(wake_id, when)
    return {"ok": True, "wake": row, "fired_immediately": False}


async def cancel_pending_auto(user_id: str | None = None) -> int:
    """把待开火的随机醒来全撤掉（重挑之前、或用户把随机关掉时）。"""
    uid = user_id or settings.user_id
    n = 0
    for row in await db.list_pending_auto_wakes(uid):
        wid = int(row["id"])
        if await db.mark_wake_cancelled(wid):
            disarm_wake(wid)
            n += 1
    return n


async def ensure_auto_wake(user_id: str | None = None) -> dict[str, Any]:
    """保证「下一次随机醒来」这条 pending 存在且仍合规。

    幂等：已经武装的那条**只要还过得了护栏就原样留着**，不重摇骰子。这条很重要，
    否则每来一条消息、每次重启都重挑一次，等于「摇到不喜欢的点就再摇一次」，
    随机也就不随机了，还会把 wakes 表刷成一长串 cancelled。

    调用点：进程启动、每轮聊天之后（用户刚说过话可能让已武装那条不再合规）、
    随机醒来开火或被挡之后、护栏设置保存之后。
    """
    uid = user_id or settings.user_id
    if _scheduler is None or not _scheduler.running:
        # 进程还没起来／已经关掉：什么都别动。pending 那条留着，下次 start 会重挂
        return {"ok": False, "armed": False, "detail": "wake scheduler not running"}

    wake_prefs = load_wake()

    on, reason = random_on(wake_prefs)
    if not on:
        n = await cancel_pending_auto(uid)
        return {"ok": True, "armed": False, "detail": reason, "cancelled": n}

    now = _utc_now()
    keep: dict[str, Any] | None = None
    for row in await db.list_pending_auto_wakes(uid):
        wid = int(row["id"])
        try:
            when = parse_wake_at(wake_at=row["wake_at"])
        except ValueError:
            when = None
        still_ok = False
        if keep is None and when is not None and when > now:
            still_ok = (await slot_allowed(uid, when, wake_prefs))[0]
        if still_ok:
            keep = row
            _arm_in_scheduler(wid, when)  # 重启后要重新挂进 APScheduler；幂等
        elif await db.mark_wake_cancelled(wid):
            disarm_wake(wid)

    if keep is not None:
        return {"ok": True, "armed": True, "detail": "kept", "wake": keep}

    when = await next_auto_wake_at(uid, wake_prefs)
    if when is None:
        return {"ok": True, "armed": False, "detail": "no_slot"}

    result = await schedule_wake(
        user_id=uid,
        wake_at=when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        intent="check_in",
        source="auto",
    )
    if not result.get("ok"):
        log.info("auto wake not armed: %s", result.get("detail"))
        return {"ok": False, "armed": False, "detail": result.get("detail")}
    log.info("auto wake armed for %s", when.isoformat())
    return {"ok": True, "armed": True, "detail": "picked", "wake": result.get("wake")}


async def reload_wake_policy(user_id: str | None = None) -> dict[str, Any]:
    """护栏设置改了之后重算下一次随机醒来（`PUT /prefs/wake` 调）。"""
    return await ensure_auto_wake(user_id)


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
        auto = await ensure_auto_wake(settings.user_id)
        log.info(
            "wake scheduler started; restored_pending=%s auto=%s(%s)",
            n,
            auto.get("armed"),
            auto.get("detail"),
        )
    else:
        log.info("wake scheduler started; proactive disabled (no arming)")


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
