"""Builtin prefs tool handlers (纠偏层).

只给模型 write / list 两个入口。**没有 prefs_delete**：删除是用户的隐私出口
（PLAN §4.2「用户可看可删」），走 `DELETE /prefs/{id}`——别让模型有能力把对方
纠正过他的话抹掉。
"""

from __future__ import annotations

from typing import Any

from app.prefs import list_style, write_style


def prefs_write_handler(
    id: str | None = None,  # noqa: A002 — tool schema 里就叫 id
    text: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    if not (id or "").strip():
        return {"ok": False, "detail": "id is required"}
    if not (text or "").strip():
        return {"ok": False, "detail": "text is required"}
    # 只回 id，不回写 text：少给他一份可以复述的素材（这层不出回执）。
    return write_style(id, text)


def prefs_list_handler(**_kwargs: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "prefs": [
            {"id": item["id"], "text": item["text"]} for item in list_style()
        ],
    }
