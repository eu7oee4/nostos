"""Builtin memory tool handlers (wired into registry).

写入拆成两个工具（2026-09-14，Notion「记忆系统设计对照」第 ⑫ 点）：

    memory_write_item(name, content)             事件 / 短事实
    memory_write_feel(name, content, intensity)  感受，intensity ∈ low / mid / high

为什么是两个工具而不是一个工具加 kind 参数：prefs_write 那次实验（issue #9）
证明了**工具描述文本是唯一的杠杆**，两个描述各自把触发例句写满，统计也天然分开。

`memory_write` 保留一个版本周期当 item 的别名——能执行，但**不再给模型看**
（不在 chat_loop.CHAT_TOOL_NAMES 里）：三个写工具摆在一起，他会挑最短的那个。

只有 write / read / list。**没有 memory_delete**：删除是用户的隐私出口
（`DELETE /memories/{id}`），和 prefs 不给模型 delete 是同一条纪律。
"""

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


def memory_write_item_handler(
    name: str = "",
    content: str = "",
    title: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    if not name:
        return {"ok": False, "detail": "name is required"}
    return write_memory(name, content, title=title, user_id=settings.user_id, kind="item")


def memory_write_feel_handler(
    name: str = "",
    content: str = "",
    intensity: str = "",
    title: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    if not name:
        return {"ok": False, "detail": "name is required"}
    if not intensity:
        return {"ok": False, "detail": "intensity is required (low / mid / high)"}
    return write_memory(
        name,
        content,
        title=title,
        user_id=settings.user_id,
        kind="feel",
        intensity=str(intensity).strip().lower(),
    )


# 旧名字，一个版本周期后删。
memory_write_handler = memory_write_item_handler
