"""Random-wake policy: quiet windows, interval, daily cap, recent chat."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app import db
from app.schedule.prefs import load_prefs


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _in_window(local: datetime, start: str, end: str) -> bool:
    """True if local time falls in [start, end) (end exclusive). Supports overnight."""
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


def in_quiet_hours(local: datetime, prefs: dict[str, Any]) -> bool:
    qh = prefs.get("quiet_hours") or {}
    if not qh.get("enabled"):
        return False
    for w in qh.get("windows") or []:
        if _in_window(local, w["start"], w["end"]):
            return True
    return False


def _tz(prefs: dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(str(prefs.get("timezone") or "Asia/Shanghai"))
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Shanghai")


def _local_now(prefs: dict[str, Any]) -> datetime:
    return datetime.now(timezone.utc).astimezone(_tz(prefs))


def _day_bounds_utc(
    prefs: dict[str, Any], local: datetime | None = None
) -> tuple[datetime, datetime]:
    loc = local or _local_now(prefs)
    start_local = loc.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def can_fire_now(
    user_id: str, prefs: dict[str, Any] | None = None
) -> tuple[bool, str]:
    """Check auto-wake rules at fire time. Manual wakes skip this."""
    p = prefs or load_prefs()
    if not p.get("proactive_enabled"):
        return False, "proactive_disabled"
    if not (p.get("random") or {}).get("enabled", True):
        return False, "random_disabled"

    now_utc = datetime.now(timezone.utc)
    local = now_utc.astimezone(_tz(p))

    if in_quiet_hours(local, p):
        return False, "quiet_hours"

    mi = p.get("min_interval") or {}
    if mi.get("enabled"):
        last = await db.last_auto_fire_at(user_id)
        if last:
            gap = timedelta(minutes=int(mi.get("minutes") or 240))
            if now_utc < last + gap:
                return False, "min_interval"

    dc = p.get("daily_cap") or {}
    if dc.get("enabled"):
        start_u, end_u = _day_bounds_utc(p, local)
        n = await db.count_auto_fires_between(user_id, start_u, end_u)
        if n >= int(dc.get("max") or 3):
            return False, "daily_cap"

    rc = p.get("recent_chat") or {}
    if rc.get("enabled"):
        last_user = await db.last_user_message_at(user_id)
        if last_user:
            cool = timedelta(minutes=int(rc.get("minutes") or 45))
            if now_utc < last_user + cool:
                return False, "recent_chat"

    return True, "ok"


def _earliest_allowed(local: datetime, prefs: dict[str, Any]) -> datetime:
    cur = local
    for _ in range(48 * 12):
        if not in_quiet_hours(cur, prefs):
            return cur
        cur += timedelta(minutes=5)
    return local + timedelta(days=1)


async def next_auto_wake_at(
    user_id: str,
    prefs: dict[str, Any] | None = None,
) -> datetime | None:
    """Pick next auto wake instant (UTC), or None if cannot schedule."""
    p = prefs or load_prefs()
    if not p.get("proactive_enabled"):
        return None
    if not (p.get("random") or {}).get("enabled", True):
        return None

    tz = _tz(p)
    now_utc = datetime.now(timezone.utc)
    local = now_utc.astimezone(tz)

    earliest = local
    mi = p.get("min_interval") or {}
    if mi.get("enabled"):
        last = await db.last_auto_fire_at(user_id)
        if last:
            gap = timedelta(minutes=int(mi.get("minutes") or 240))
            candidate = (last + gap).astimezone(tz)
            if candidate > earliest:
                earliest = candidate

    rc = p.get("recent_chat") or {}
    if rc.get("enabled"):
        last_user = await db.last_user_message_at(user_id)
        if last_user:
            cool = timedelta(minutes=int(rc.get("minutes") or 45))
            candidate = (last_user + cool).astimezone(tz)
            if candidate > earliest:
                earliest = candidate

    dc = p.get("daily_cap") or {}
    if dc.get("enabled"):
        start_u, end_u = _day_bounds_utc(p, local)
        n = await db.count_auto_fires_between(user_id, start_u, end_u)
        if n >= int(dc.get("max") or 3):
            tomorrow = (local + timedelta(days=1)).replace(
                hour=0, minute=5, second=0, microsecond=0
            )
            if tomorrow > earliest:
                earliest = tomorrow

    earliest = _earliest_allowed(earliest, p)

    horizon_h = int((p.get("random") or {}).get("max_horizon_hours") or 18)
    latest = now_utc.astimezone(tz) + timedelta(hours=horizon_h)
    if earliest >= latest:
        latest = earliest + timedelta(hours=6)

    span_sec = max(60, int((latest - earliest).total_seconds()))
    for _ in range(40):
        offset = random.randint(0, span_sec)
        cand = _earliest_allowed(earliest + timedelta(seconds=offset), p)
        if dc.get("enabled") and cand.astimezone(tz).date() == local.date():
            s_u, e_u = _day_bounds_utc(p, cand)
            n = await db.count_auto_fires_between(user_id, s_u, e_u)
            if n >= int(dc.get("max") or 3):
                continue
        return cand.astimezone(timezone.utc)

    return _earliest_allowed(earliest, p).astimezone(timezone.utc)
