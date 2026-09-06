"""In-process wake scheduler (APScheduler). Domain language: wake, never job."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import db
from app.config import settings
from app.llm import LLMError, chat_completion
from app.memory import recall_text
from app.schedule.policy import can_fire_now, next_auto_wake_at
from app.schedule.prefs import load_prefs, proactive_on

log = logging.getLogger("nostos.wake")

_scheduler: AsyncIOScheduler | None = None
_PATROL_KEY = "wake-policy-patrol"


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
    if _scheduler is None:
        raise RuntimeError("wake scheduler not started")
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
    try:
        _scheduler.remove_job(_wake_key(wake_id))
    except Exception:  # noqa: BLE001
        pass


async def fire_wake(wake_id: int) -> None:
    row = await db.get_wake(wake_id)
    if not row or row["status"] != "pending":
        return

    uid = row["user_id"]
    source = (row.get("source") or "manual").strip()
    prefs = load_prefs()

    if source == "auto":
        ok, reason = await can_fire_now(uid, prefs)
        if not ok:
            await db.mark_wake_cancelled(wake_id)
            log.info("auto wake %s skipped: %s", wake_id, reason)
            await ensure_auto_wake(uid)
            return

    note = (row.get("note") or "").strip()
    intent = (row.get("intent") or "check_in").strip()
    text = note if note else await _generate_wake_line(uid, intent)

    await db.add_message(uid, "assistant", text)
    await db.mark_wake_fired(wake_id)
    log.info("wake fired id=%s user=%s source=%s", wake_id, uid, source)

    if source == "auto":
        await ensure_auto_wake(uid)


async def _generate_wake_line(user_id: str, intent: str) -> str:
    prompt = (
        "你是 nostos，用户的 AI 伙伴。现在到了你主动来找用户的时刻。\n"
        f"意图：{intent}\n"
        "根据记忆写一句很短的打招呼（1～2 句），自然、不工具腔。"
        "不要提工具、调度或系统。"
    )
    messages = [
        {"role": "system", "content": prompt},
        {"role": "system", "content": "【当前记忆召回】\n" + recall_text(user_id)},
        {"role": "user", "content": "来找我一下吧。"},
    ]
    try:
        msg = await chat_completion(messages, tools=None)
        text = (msg.get("content") or "").strip()
        return text or "嘿，我来看你啦。"
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
    prefs = load_defaults()
    src = source if source in ("manual", "auto") else "manual"

    if not proactive_on(prefs):
        return {
            "ok": False,
            "detail": "proactive wakes are off — enable in Settings",
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
        uid, iso, note=note_clean, intent=intent or "check_in", source=src
    )
    wake_id = int(row["id"])

    if when <= _utc_now():
        await fire_wake(wake_id)
        fresh = await db.get_wake(wake_id)
        return {"ok": True, "wake": fresh, "fired_immediately": True}

    _arm_in_scheduler(wake_id, when)
    return {"ok": True, "wake": row, "fired_immediately": False}


async def cancel_pending_auto(user_id: str | None = None) -> int:
    uid = user_id or settings.user_id
    rows = await db.list_pending_auto_wakes(uid)
    n = 0
    for row in rows:
        wid = int(row["id"])
        if await db.mark_wake_cancelled(wid):
            disarm_wake(wid)
            n += 1
    return n


async def ensure_auto_wake(user_id: str | None = None) -> dict[str, Any]:
    uid = user_id or settings.user_id
    prefs = load_defaults()
    await cancel_pending_auto(uid)

    if not proactive_on(prefs):
        return {"ok": True, "armed": False, "detail": "proactive_off"}
    if not (prefs.get("random") or {}).get("enabled", True):
        return {"ok": True, "armed": False, "detail": "random_off"}

    when = await next_auto_wake_at(uid, prefs)
    if when is None:
        return {"ok": True, "armed": False, "detail": "no_slot"}

    iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    result = await schedule_wake(
        user_id=uid,
        wake_at=iso,
        intent="check_in",
        note=None,
        source="auto",
    )
    return {"ok": bool(result.get("ok")), "armed": bool(result.get("ok")), "result": result}


async def restore_pending_wakes() -> int:
    prefs = load_defaults()
    if not proactive_on(prefs):
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


async def _policy_patrol() -> None:
    await ensure_auto_wake(settings.user_id)


def _setup_patrol(prefs: dict[str, Any]) -> None:
    if _scheduler is None:
        return
    minutes = int(prefs.get("policy_patrol_minutes") or 0)
    try:
        _scheduler.remove_job(_PATROL_KEY)
    except Exception:  # noqa: BLE001
        pass
    if minutes <= 0:
        return
    _scheduler.add_job(
        _policy_patrol,
        trigger="interval",
        minutes=minutes,
        id=_PATROL_KEY,
        replace_existing=True,
    )


async def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    _scheduler = AsyncIOScheduler(timezone=timezone.utc)
    _scheduler.start()
    prefs = load_defaults()
    n = await restore_pending_wakes()
    if proactive_on(prefs):
        await ensure_auto_wake(settings.user_id)
    _setup_patrol(prefs)
    log.info(
        "wake scheduler started; restored_pending=%s proactive=%s",
        n,
        proactive_on(prefs),
    )


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


async def reload_wake_policy() -> dict[str, Any]:
    prefs = load_defaults()
    _setup_patrol(prefs)
    return await ensure_auto_wake(settings.user_id)
