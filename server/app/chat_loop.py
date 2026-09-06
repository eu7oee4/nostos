"""Chat: history + recall + memory/wake tool_use loop."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app import db
from app.config import settings
from app.llm import chat_completion
from app.memory import recall_text
from app.nostools.registry import registry
from app.schedule.scheduler import ensure_auto_wake

log = logging.getLogger("nostos.chat")

CHAT_TOOL_NAMES = [
    "memory_list",
    "memory_read",
    "memory_write",
    "wake_set",
    "wake_list",
    "wake_cancel",
]
MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = (
    "你是 nostos，用户的 AI 伙伴（不是助手工具箱）。说话自然、简短。\n"
    "你有长期记忆（markdown 文件）。用户说出值得长期记住的事实时，"
    "用 memory_write 写入（短 id，如 name / hometown / preferences）；"
    "需要核对细节时用 memory_read / memory_list。\n"
    "你可以预约主动来找用户：wake_set（测试可用 delay_seconds；"
    "可带 note 写死台词，或只带 intent 到点再生成）。"
    "wake_list / wake_cancel 查看或取消。主动触达需在设置里开启 proactive。\n"
    "不要把工具过程念给用户听；不要编造未写入的记忆。"
    "闲聊不必强行写记忆或设 wake。"
)


async def _run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    spec = registry.tools.get(name)
    if not spec or not spec.handler:
        return {"ok": False, "detail": f"unknown tool: {name}"}
    if name not in CHAT_TOOL_NAMES:
        return {"ok": False, "detail": f"tool not available: {name}"}
    try:
        result = spec.handler(**arguments)
        if asyncio.iscoroutine(result):
            result = await result
        return result if isinstance(result, dict) else {"ok": True, "result": result}
    except TypeError as e:
        return {"ok": False, "detail": f"bad arguments: {e}"}
    except Exception as e:  # noqa: BLE001 — surface to model
        return {"ok": False, "detail": str(e)}


def _parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


async def _maybe_rearm_auto(uid: str) -> None:
    """After chat, re-date-arm next auto wake (recent_chat cool-down changes)."""
    try:
        await ensure_auto_wake(uid)
    except Exception as e:  # noqa: BLE001 — never break chat
        log.warning("ensure_auto_wake after chat failed: %s", e)


async def run_chat(user_text: str) -> dict[str, Any]:
    uid = settings.user_id
    await db.add_message(uid, "user", user_text)
    history = await db.history_for_llm(uid, limit=40)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
        {
            "role": "system",
            "content": (
                "【当前记忆召回】（只读快照；写入请用 memory_write）\n"
                + recall_text(uid)
            ),
        },
    ]

    tools = registry.openai_tools(CHAT_TOOL_NAMES)
    touched: list[str] = []
    wakes_touched: list[int] = []

    for _ in range(MAX_TOOL_ROUNDS):
        msg = await chat_completion(messages, tools=tools, tool_choice="auto")
        tool_calls = msg.get("tool_calls") or []

        if tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or None,
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                args = _parse_args(fn.get("arguments"))
                result = await _run_tool(name, args)
                if name == "memory_write" and result.get("ok"):
                    touched.append(str(result.get("id") or args.get("name") or ""))
                if name == "wake_set" and result.get("ok"):
                    w = result.get("wake") or {}
                    if w.get("id") is not None:
                        wakes_touched.append(int(w["id"]))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            continue

        reply = (msg.get("content") or "").strip()
        assistant = await db.add_message(uid, "assistant", reply)
        await _maybe_rearm_auto(uid)
        return {
            "user_id": uid,
            "message": assistant,
            "memories_touched": [t for t in touched if t],
            "wakes_touched": wakes_touched,
        }

    msg = await chat_completion(messages, tools=None)
    reply = (msg.get("content") or "").strip() or "（这轮工具次数用尽了，再说一次试试）"
    assistant = await db.add_message(uid, "assistant", reply)
    await _maybe_rearm_auto(uid)
    return {
        "user_id": uid,
        "message": assistant,
        "memories_touched": [t for t in touched if t],
        "wakes_touched": wakes_touched,
    }
