"""Builtin memory tool handlers (wired into registry)."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.memory import list_memories, read_memory, write_memory


def memory_list_handler(**_kwargs: Any) -> dict[str, Any]:
    items = list_memories(settings.user_id)
    return {"ok": True, "user_id": settings.user_id, "memories": items}


def memory_read_handler(name: str = "", **_kwargs: Any) -> dict[str, Any]:
    if not name:
        return {"ok": False, "detail": "name is required"}
    return read_memory(name, settings.user_id)


def memory_write_handler(
    name: str = "",
    content: str = "",
    title: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    if not name:
        return {"ok": False, "detail": "name is required"}
    return write_memory(name, content, title=title, user_id=settings.user_id)
