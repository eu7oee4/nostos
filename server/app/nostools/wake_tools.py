"""Builtin wake tool handlers."""

from __future__ import annotations

import asyncio
from typing import Any

from app import db
from app.config import settings
from app.schedule.scheduler import disarm_wake, schedule_wake


def _run(coro: Any) -> Any:
    """Run async wake helpers from sync tool handlers."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Inside running loop (chat request): schedule and wait via future
    return asyncio.ensure_future(coro)  # type: ignore[return-value]


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
    items = await db.list_wakes(settings.user_id, status="pending")
    return {
        "ok": True,
        "proactive_enabled": settings.proactive_enabled,
        "wakes": items,
    }


async def wake_cancel_handler(wake_id: int | str | None = None, **_kwargs: Any) -> dict[str, Any]:
    if wake_id is None:
        return {"ok": False, "detail": "wake_id is required"}
    try:
        wid = int(wake_id)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "wake_id must be an integer"}
    ok = await db.mark_wake_cancelled(wid)
    if ok:
        disarm_wake(wid)
    return {"ok": ok, "wake_id": wid, "detail": None if ok else "not found or not pending"}
