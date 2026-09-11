"""In-process wake scheduler (APScheduler). Our API says wake, never job."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import db, push
from app.config import settings
from app.context.assemble import Trigger, build_messages
from app.context.scrub import scrub_reply
from app.llm import LLMError, chat_completion
from app.locks import turn_lock
from app.memory import recall_text
from app.prefs import load_wake
from app.schedule.policy import (
    can_fire_now,
    in_quiet_hours,
    next_auto_wake_at,
    random_on,
    slot_allowed,
)
from app.trace import new_turn

log = logging.getLogger("nostos.wake")

# 过了点多久还算「现在开」。APScheduler 自己那条 misfire_grace_time 只管进程活着
# 时的误点；重启后 `restore_pending_wakes` 走的是我们自己的路，得用同一个数。
# 停机三天再起来，三天前那条「嘿，我来看你啦」不该再推——那不是主动来找你，是
# 迟到的机器。
MISFIRE_GRACE_SECONDS = 300

FALLBACK_WAKE_LINE = "嘿，我来看你啦。"

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
        return _utc_now() + timedelta(seconds=sec)
    if not wake_at or not str(wake_at).strip():
        raise ValueError("provide wake_at (ISO UTC) or delay_seconds")
    raw = str(wake_at).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def classify_restore(
    when: datetime,
    now: datetime,
    grace_seconds: int = MISFIRE_GRACE_SECONDS,
) -> Literal["arm", "missed"]:
    """重启时一条 pending 该怎么处理：还没到点 / 过点但在宽限内 → 挂回去（过点的
    立刻开）；过点超过宽限 → skipped(missed)。纯函数，测试直接打这里。"""
    if when <= now - timedelta(seconds=grace_seconds):
        return "missed"
    return "arm"


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
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
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
    new_turn("wake")

    # 整轮持锁（app/locks.py）：正在聊天就等那轮说完再开口，别插进对话中间。
    # 等锁期间这条可能被取消了，所以拿到锁之后护栏和状态都要**重看一遍**。
    async with turn_lock(uid):
        fresh = await db.get_wake(wake_id)
        if not fresh or fresh["status"] != "pending":
            log.info("wake id=%s closed while waiting for turn lock", wake_id)
            return

        wake_prefs = load_wake()

        # 随机醒来到点还要**再过一遍护栏**：武装的那一刻合规，不代表现在还合规
        # （中间用户可能刚聊过、或已经被别的随机醒来吃掉了今天的额度）。
        # 挡下来就记 skipped + 原因、重挑，**一个 token 都不烧**。
        if source == "auto":
            ok, reason = await can_fire_now(uid, wake_prefs)
            if not ok:
                await db.mark_wake_skipped(wake_id, reason)
                log.info("auto wake id=%s skipped before generating: %s", wake_id, reason)
                await ensure_auto_wake(uid)
                return

        note = (fresh.get("note") or "").strip()
        intent = (fresh.get("intent") or "check_in").strip()

        if note:
            text = note
        else:
            text = await _generate_wake_line(uid, intent)

        # 标 fired + 写消息是一个事务（db.fire_wake_tx）。返回 None = 这条已经不是
        # pending 了（并发开火 / 刚被取消），什么都没写，也不推。
        delivered = await db.fire_wake_tx(wake_id, uid, text)

    if delivered is None:
        log.warning("wake id=%s not pending at commit; nothing delivered", wake_id)
        return
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

    模型挂了就用兜底那句，但**要记日志**：静默降级和有意的降级，差别就在留痕。
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
    except LLMError as e:
        log.warning("wake line generation failed (%s); using fallback line", e)
        return FALLBACK_WAKE_LINE
    text = scrub_reply((msg.get("content") or "").strip())
    if not text:
        log.warning("wake line came back empty after scrub; using fallback line")
        return FALLBACK_WAKE_LINE
    return text


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

    到点已过的（delay 0 / 过去的时刻）**不在这里 inline 开火**，挂进调度器让它
    下一拍就跑。原因是锁：模型在聊天里 `wake_set(delay_seconds=0)` 时，聊天这轮
    正持着 turn 锁，inline 调 `fire_wake` 会在同一把锁上死等。
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

    now = _utc_now()
    due_now = when <= now
    _arm_in_scheduler(wake_id, max(when, now))
    return {"ok": True, "wake": row, "due_now": due_now}


async def cancel_pending_auto(user_id: str | None = None, reason: str | None = None) -> int:
    """把待开火的随机醒来全撤掉（用户把随机关掉时）。"""
    uid = user_id or settings.user_id
    n = 0
    for row in await db.list_pending_auto_wakes(uid):
        wid = int(row["id"])
        if await db.mark_wake_cancelled(wid, reason):
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
        n = await cancel_pending_auto(uid, reason)
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
        elif await db.mark_wake_skipped(wid, "repick"):
            # 系统重挑的，不是人取消的：记 skipped(repick)
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


async def restore_pending_wakes() -> dict[str, int]:
    """重启后把 pending 挂回调度器。

    过点超过 MISFIRE_GRACE_SECONDS 的**不补开**，记 skipped(missed)。以前是
    「过点的全部立刻开火」：服务器停三天再起来，用户一口气收到停机期间攒的每一条
    推送，而且每条都要调一次模型。宽限内的挂到「现在」，下一拍就开。
    """
    if not settings.proactive_enabled:
        return {"armed": 0, "missed": 0}
    rows = await db.list_wakes(settings.user_id, status="pending", limit=200)
    armed = missed = 0
    now = _utc_now()
    for row in rows:
        wake_id = int(row["id"])
        try:
            when = parse_wake_at(wake_at=row["wake_at"])
        except ValueError:
            await db.mark_wake_skipped(wake_id, "bad_wake_at")
            log.warning("wake id=%s has unparsable wake_at %r; skipped", wake_id, row["wake_at"])
            continue
        if classify_restore(when, now) == "missed":
            await db.mark_wake_skipped(wake_id, "missed")
            log.info("wake id=%s missed while down (due %s); skipped", wake_id, row["wake_at"])
            missed += 1
        else:
            _arm_in_scheduler(wake_id, max(when, now))
            armed += 1
    return {"armed": armed, "missed": missed}


async def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    _scheduler = AsyncIOScheduler(timezone=timezone.utc)
    _scheduler.start()
    if settings.proactive_enabled:
        restored = await restore_pending_wakes()
        auto = await ensure_auto_wake(settings.user_id)
        log.info(
            "wake scheduler started; restored=%s missed=%s auto=%s(%s)",
            restored["armed"],
            restored["missed"],
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
