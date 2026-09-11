"""Chat: assemble pipeline + memory/wake tool_use loop."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app import db
from app.config import settings
from app.context.assemble import Trigger, build_messages
from app.context.scrub import scrub_reply
from app.llm import chat_completion
from app.memory import recall_text
from app.nostools.registry import registry
from app.schedule.scheduler import ensure_auto_wake

log = logging.getLogger("nostos.chat")

CHAT_TOOL_NAMES = [
    "memory_list",
    "memory_read",
    "memory_write",
    "prefs_write",
    "prefs_list",
    "wake_set",
    "wake_list",
    "wake_cancel",
]
MAX_TOOL_ROUNDS = 4


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


async def _finish(
    uid: str,
    assistant: dict[str, Any],
    touched: list[str],
    wakes_touched: list[int],
) -> dict[str, Any]:
    """回复已经落库，收口前把「下一次随机醒来」对一下表。

    用户刚说过话，已武装的那条随机醒来可能正好撞进 `recent_chat` 冷却里——
    这时重挑一个点。仍合规就原样留着（`ensure_auto_wake` 幂等，不会每条消息
    都重摇骰子）。

    **绝不能影响这次回复**：随机醒来是补充路径，炸了只记日志——用户等的是那句话。
    """
    try:
        await ensure_auto_wake(uid)
    except Exception:  # noqa: BLE001
        log.warning("ensure_auto_wake after chat failed", exc_info=True)
    return {
        "user_id": uid,
        "message": assistant,
        "memories_touched": [t for t in touched if t],
        "wakes_touched": wakes_touched,
    }


async def run_chat(user_text: str) -> dict[str, Any]:
    uid = settings.user_id
    await db.add_message(uid, "user", user_text)
    turns = await db.list_recent_turns(uid, limit=40)
    # Prior turns only — current user line is carried by Trigger (no duplicate).
    prior = turns[:-1] if turns else []

    messages = build_messages(
        user_id=uid,
        history_rows=prior,
        recall=recall_text(uid),
        trigger=Trigger(kind="user", text=user_text),
    )

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

        reply = scrub_reply((msg.get("content") or "").strip())
        assistant = await db.add_message(uid, "assistant", reply)
        return await _finish(uid, assistant, touched, wakes_touched)

    msg = await chat_completion(messages, tools=None)
    reply = scrub_reply((msg.get("content") or "").strip()) or "（这轮工具次数用尽了，再说一次试试）"
    assistant = await db.add_message(uid, "assistant", reply)
    return await _finish(uid, assistant, touched, wakes_touched)
