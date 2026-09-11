"""随机醒来的护栏与选点（#10）。

来历：`feat/random-wake` 分支上的 `schedule/policy.py`。那条分支的接线部分和 #5/#6
重写过的 scheduler / chat_loop / db 冲突到没法 rebase，所以只把**策略逻辑**摘过来，
在新 scheduler 上重接（issue #10）。两处和原版不同：

1. 偏好读 `app.prefs` 的 `wake{}` 段（#9 建的那套），不再有第二份 `schedule/prefs.py`
2. 总开关仍是 `.env` 的 `PROACTIVE_ENABLED`，时区仍是 `settings.timezone`——
   不在 prefs 里再开一个，免得同一件事两个真相来源

**这层不是决策者**（DESIGN §6）。伙伴用 `wake_set` 定的时刻照定、照到点执行；这里
只管「随机醒来」这条补充路径挑哪个点、到点还能不能开火。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app import db
from app.config import settings
from app.prefs import load_wake

# 挑点时往前挪出安静时段用的步长 / 上限（2 天足够跨过任何合法窗口组合）
_STEP = timedelta(minutes=5)
_MAX_STEPS = 2 * 24 * 12
# 随机取样次数：取不到就退回「最早允许的那一刻」，不无限转
_TRIES = 40
# 挑出来的点至少离现在多远
_MIN_LEAD = timedelta(minutes=1)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except Exception:  # noqa: BLE001 — 配错时区不该让 wake 整条挂掉
        return ZoneInfo("UTC")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _in_window(local: datetime, start: str, end: str) -> bool:
    """本地时刻是否落在 [start, end) 内。start > end 视为跨夜（23:00-08:00）。"""
    sh, sm = _parse_hhmm(start)
    eh, em = _parse_hhmm(end)
    minutes = local.hour * 60 + local.minute
    start_m = sh * 60 + sm
    end_m = eh * 60 + em
    if start_m == end_m:
        return False
    if start_m < end_m:
        return start_m <= minutes < end_m
    return minutes >= start_m or minutes < end_m


def in_quiet_hours(when_utc: datetime, prefs: dict[str, Any] | None = None) -> bool:
    """这一刻在不在安静时段里（按显示时区判）。"""
    p = prefs or load_wake()
    qh = p.get("quiet_hours") or {}
    if not qh.get("enabled"):
        return False
    local = when_utc.astimezone(_tz())
    return any(
        _in_window(local, w["start"], w["end"]) for w in (qh.get("windows") or [])
    )


def _day_bounds_utc(when_utc: datetime) -> tuple[datetime, datetime]:
    """这一刻所在**本地日**的 [00:00, 次日 00:00)，换算成 UTC。"""
    local = when_utc.astimezone(_tz())
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def slot_allowed(
    user_id: str,
    when_utc: datetime,
    prefs: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """四条护栏：这一刻能不能安排/开火一次**随机**醒来。

    同一个判断既用来挑点、也用来到点复查、还用来判断已武装那条要不要重挑——
    三处口径必须一样，所以只有这一个实现。
    """
    p = prefs or load_wake()

    if in_quiet_hours(when_utc, p):
        return False, "quiet_hours"

    mi = p.get("min_interval") or {}
    if mi.get("enabled"):
        last = await db.last_auto_fire_at(user_id)
        if last and when_utc < last + timedelta(minutes=int(mi.get("minutes") or 240)):
            return False, "min_interval"

    rc = p.get("recent_chat") or {}
    if rc.get("enabled"):
        last_user = await db.last_user_message_at(user_id)
        if last_user and when_utc < last_user + timedelta(
            minutes=int(rc.get("minutes") or 45)
        ):
            return False, "recent_chat"

    dc = p.get("daily_cap") or {}
    if dc.get("enabled"):
        start_u, end_u = _day_bounds_utc(when_utc)
        n = await db.count_auto_fires_between(user_id, start_u, end_u)
        if n >= int(dc.get("max") or 3):
            return False, "daily_cap"

    return True, "ok"


def random_on(prefs: dict[str, Any] | None = None) -> tuple[bool, str]:
    """两个总开关：`.env` 的 PROACTIVE_ENABLED、prefs 的 `wake.random.enabled`。"""
    p = prefs or load_wake()
    if not settings.proactive_enabled:
        return False, "proactive_disabled"
    if not (p.get("random") or {}).get("enabled", True):
        return False, "random_disabled"
    return True, "ok"


async def can_fire_now(
    user_id: str,
    prefs: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """到点复查。挡下来的那条随机醒来**根本不调模型**，直接作废重挑。"""
    p = prefs or load_wake()
    ok, reason = random_on(p)
    if not ok:
        return False, reason
    return await slot_allowed(user_id, _utc_now(), p)


def _out_of_quiet(when_utc: datetime, prefs: dict[str, Any]) -> datetime:
    """把一个落在安静时段里的时刻往后挪到窗口外。**只用来算「最早允许」**——
    挑点时不能用它（见 next_auto_wake_at 里为什么是重摇不是挪）。"""
    cur = when_utc
    for _ in range(_MAX_STEPS):
        if not in_quiet_hours(cur, prefs):
            return cur
        cur += _STEP
    return when_utc + timedelta(days=1)


async def next_auto_wake_at(
    user_id: str,
    prefs: dict[str, Any] | None = None,
) -> datetime | None:
    """挑下一次随机醒来的时刻（UTC）；挑不出来回 None。

    先按最小间隔 / 刚聊过 / 日上限算出「最早允许」，再在 [最早, 最早+horizon] 里
    掷骰子，**落进安静时段就重摇**，直到 `slot_allowed` 点头。

    重摇而不是「挪到窗口边缘」：默认安静时段 23:00-08:00 占掉 18 小时窗口里的一
    半，挪的话有一半的点会堆在 08:00 那一分钟上——那不叫随机醒来，那叫每天早八
    准时打卡，活人感正好死在这里。重摇 40 次都撞上的概率可以忽略（一半可用时是
    1e-12），真撞上了才退回「最早允许的那一刻」。
    """
    p = prefs or load_wake()
    if not random_on(p)[0]:
        return None

    now = _utc_now()
    # 至少留一分钟：挑到「就是现在」会让 schedule_wake 走立即开火那条分支，
    # 开完又回头 ensure_auto_wake——护栏全关时这就是一条无限递归。
    earliest = now + _MIN_LEAD

    mi = p.get("min_interval") or {}
    if mi.get("enabled"):
        last = await db.last_auto_fire_at(user_id)
        if last:
            earliest = max(
                earliest, last + timedelta(minutes=int(mi.get("minutes") or 240))
            )

    rc = p.get("recent_chat") or {}
    if rc.get("enabled"):
        last_user = await db.last_user_message_at(user_id)
        if last_user:
            earliest = max(
                earliest, last_user + timedelta(minutes=int(rc.get("minutes") or 45))
            )

    dc = p.get("daily_cap") or {}
    if dc.get("enabled"):
        start_u, end_u = _day_bounds_utc(earliest)
        n = await db.count_auto_fires_between(user_id, start_u, end_u)
        if n >= int(dc.get("max") or 3):
            # 今天的额度用完了，最早也是明天本地 00:00 之后
            earliest = max(earliest, end_u)

    earliest = _out_of_quiet(earliest, p)
    horizon = int((p.get("random") or {}).get("max_horizon_hours") or 18)
    span = max(60, horizon * 3600)

    for _ in range(_TRIES):
        cand = earliest + timedelta(seconds=random.randint(0, span))
        ok, _reason = await slot_allowed(user_id, cand, p)
        if ok:
            return cand

    ok, _reason = await slot_allowed(user_id, earliest, p)
    return earliest if ok else None
