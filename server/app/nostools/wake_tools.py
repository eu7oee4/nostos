"""Builtin wake tool handlers (async)."""

from __future__ import annotations

from typing import Any

from app import db
from app.config import settings
from app.schedule.scheduler import disarm_wake, schedule_wake


async def wake_set_handler(
    delay_seconds: int | float | None = None,
    wake_at: str | None = None,
    note: str | None = None,
    intent: str = "check_in",
    **_kwargs: Any,
) -> dict[str, Any]:
    return await schedule_wake(
        delay_seconds=delay_seconds,
        wake_at=wake_at,
        note=note,
        intent=intent,
    )


async def wake_list_handler(**_kwargs: Any) -> dict[str, Any]:
    """他自己定的那些。**不列随机醒来**（source=auto）。

    随机醒来不是他约的，他也管不了：`wake_cancel` 掉一条 auto，下一轮聊天
    `ensure_auto_wake` 又给他补一条回来——工具回了 ok 却什么都没变，是最难查的
    那种。护栏和随机路径的出口在 `/wakes`、`/health`、`GET /prefs/wake`，那是
    用户的东西，不是他的。
    """
    items = await db.list_wakes(settings.user_id, status="pending", source="manual")
    return {
        "ok": True,
        "proactive_enabled": settings.proactive_enabled,
        "wakes": items,
    }


async def wake_cancel_handler(
    wake_id: int | str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    if wake_id is None:
        return {"ok": False, "detail": "wake_id is required"}
    try:
        wid = int(wake_id)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "wake_id must be an integer"}
    ok = await db.mark_wake_cancelled(wid)
    if ok:
        disarm_wake(wid)
    return {
        "ok": ok,
        "wake_id": wid,
        "detail": None if ok else "not found or not pending",
    }
