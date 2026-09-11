"""随机醒来的四条护栏，一条一个边界。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import db
from app.prefs import DEFAULT_WAKE
from app.schedule.policy import _in_window, in_quiet_hours, slot_allowed

SH = ZoneInfo("Asia/Shanghai")


def _local(h: int, m: int = 0) -> datetime:
    return datetime(2026, 9, 8, h, m, tzinfo=SH)


def _prefs(**overrides):
    """四条护栏默认全关，测哪条开哪条——别让别的护栏替被测的那条挡下来。"""
    p = deepcopy(DEFAULT_WAKE)
    for key in ("quiet_hours", "min_interval", "daily_cap", "recent_chat"):
        p[key]["enabled"] = False
    for key, patch in overrides.items():
        p[key].update(patch)
    return p


def test_cross_midnight_window():
    assert _in_window(_local(23, 30), "23:00", "08:00")
    assert _in_window(_local(0, 0), "23:00", "08:00")
    assert _in_window(_local(7, 59), "23:00", "08:00")
    assert not _in_window(_local(8, 0), "23:00", "08:00")
    assert not _in_window(_local(12, 0), "23:00", "08:00")


def test_same_day_window_and_empty_window():
    assert _in_window(_local(13, 0), "12:00", "14:00")
    assert not _in_window(_local(14, 0), "12:00", "14:00")
    assert not _in_window(_local(12, 0), "12:00", "12:00")


def test_quiet_hours_respects_enabled_flag():
    three_am = _local(3, 0).astimezone(timezone.utc)
    assert in_quiet_hours(three_am, _prefs(quiet_hours={"enabled": True}))
    assert not in_quiet_hours(three_am, _prefs())


@pytest.mark.anyio
async def test_quiet_hours_blocks_slot(data_dir):
    await db.init_db()
    three_am = _local(3, 0).astimezone(timezone.utc)
    ok, reason = await slot_allowed("local", three_am, _prefs(quiet_hours={"enabled": True}))
    assert (ok, reason) == (False, "quiet_hours")
    ok, reason = await slot_allowed("local", _local(14, 0).astimezone(timezone.utc), _prefs(quiet_hours={"enabled": True}))
    assert (ok, reason) == (True, "ok")


@pytest.mark.anyio
async def test_recent_chat_cooldown(data_dir):
    await db.init_db()
    await db.add_message("local", "user", "在吗")
    now = datetime.now(timezone.utc)
    p = _prefs(recent_chat={"enabled": True, "minutes": 45})
    assert await slot_allowed("local", now + timedelta(minutes=10), p) == (False, "recent_chat")
    assert await slot_allowed("local", now + timedelta(minutes=50), p) == (True, "ok")


@pytest.mark.anyio
async def test_min_interval_counts_only_auto_fires(data_dir):
    await db.init_db()
    now = datetime.now(timezone.utc)
    p = _prefs(min_interval={"enabled": True, "minutes": 240})

    # manual 开火不算
    manual = await db.create_wake("local", "2026-09-08T00:00:00Z", note="x", source="manual")
    assert await db.fire_wake_tx(manual["id"], "local", "x")
    assert await slot_allowed("local", now + timedelta(hours=1), p) == (True, "ok")

    auto = await db.create_wake("local", "2026-09-08T00:00:00Z", source="auto")
    assert await db.fire_wake_tx(auto["id"], "local", "y")
    assert await slot_allowed("local", now + timedelta(hours=1), p) == (False, "min_interval")
    assert await slot_allowed("local", now + timedelta(hours=5), p) == (True, "ok")


@pytest.mark.anyio
async def test_daily_cap(data_dir):
    await db.init_db()
    p = _prefs(daily_cap={"enabled": True, "max": 1})
    now = datetime.now(timezone.utc)
    assert await slot_allowed("local", now, p) == (True, "ok")

    auto = await db.create_wake("local", "2026-09-08T00:00:00Z", source="auto")
    assert await db.fire_wake_tx(auto["id"], "local", "y")
    await asyncio.sleep(0.01)
    # 和刚开火的那条同一刻 → 同一个本地日，额度已满
    assert await slot_allowed("local", now, p) == (False, "daily_cap")
