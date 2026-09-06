"""Min-memory chat: history + recall tail + memory tool_use loop."""

from __future__ import annotations

import json
from typing import Any

from app import db
from app.config import settings
from app.llm import chat_completion
from app.memory import recall_text
from app.nostools.registry import registry

MEMORY_TOOL_NAMES = ["memory_list", "memory_read", "memory_write"]
MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = (
    "你是 nostos，用户的 AI 伙伴（不是助手工具箱）。说话自然、简短。\n"
    "你有长期记忆（markdown 文件）。用户说出值得长期记住的事实时，"
    "用 memory_write 写入（短 id，如 name / hometown / preferences）；"
    "需要核对细节时用 memory_read / memory_list。\n"
    "不要把工具过程念给用户听；不要编造未写入的记忆。"
    "闲聊不必强行写记忆。"
)


def _run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    spec = registry.tools.get(name)
    if not spec or not spec.handler:
        return {"ok": False, "detail": f"unknown tool: {name}"}
    if name not in MEMORY_TOOL_NAMES:
        return {"ok": False, "detail": f"tool not available in min-memory: {name}"}
    try:
        result = spec.handler(**arguments)
        if hasattr(result, "__await__"):
            raise TypeError("async handlers not supported yet")
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

    tools = registry.openai_tools(MEMORY_TOOL_NAMES)
    touched: list[str] = []

    for _ in range(MAX_TOOL_ROUNDS):
        msg = await chat_completion(messages, tools=tools, tool_choice="auto")
        tool_calls = msg.get("tool_calls") or []

        if tool_calls:
            # Keep assistant tool-call message for the provider
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
                result = _run_tool(name, args)
                if name == "memory_write" and result.get("ok"):
                    touched.append(str(result.get("id") or args.get("name") or ""))
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
        return {
            "user_id": uid,
            "message": assistant,
            "memories_touched": [t for t in touched if t],
        }

    # Tool round exhausted — ask once more without tools
    msg = await chat_completion(messages, tools=None)
    reply = (msg.get("content") or "").strip() or "（这轮工具次数用尽了，再说一次试试）"
    assistant = await db.add_message(uid, "assistant", reply)
    return {
        "user_id": uid,
        "message": assistant,
        "memories_touched": [t for t in touched if t],
    }
