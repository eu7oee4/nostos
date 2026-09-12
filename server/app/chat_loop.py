"""Chat: assemble pipeline + memory/wake tool_use loop."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app import db
from app.config import settings
from app.context.assemble import Trigger, build_messages
from app.context.scrub import scrub_reply
from app.llm import LLMError, chat_completion
from app.locks import turn_lock
from app.memory import recall_text
from app.nostools.registry import registry
from app.schedule.scheduler import ensure_auto_wake
from app.trace import new_turn

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
_ARGS_LOG_CHARS = 200


class TurnFailed(Exception):
    """这轮没等到回复。user 行已经标成 failed + reason，这里把 id 带给路由，
    前端拿着它画「重发」。`cause` 是原异常（多半是 LLMError），路由据此挑状态码。"""

    def __init__(self, user_message_id: int, reason: str, cause: BaseException | None):
        self.user_message_id = user_message_id
        self.reason = reason
        self.cause = cause
        super().__init__(f"turn failed ({reason}) user_message_id={user_message_id}")


class RetryRefused(Exception):
    """重发被拒：not_found / not_failed / not_latest（见 db.reopen_turn）。"""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def _brief(arguments: dict[str, Any]) -> str:
    """留痕用的参数摘要：一行、截断。记忆正文那种长字段不整段进日志。"""
    try:
        s = json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        s = repr(arguments)
    return s if len(s) <= _ARGS_LOG_CHARS else s[: _ARGS_LOG_CHARS - 1] + "…"


async def _run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """唯一的动作执行入口（StarHub 那条「单一动作执行入口」）。

    side_effect 在这里生效：
      outbound  没有批准通道 → 拒绝 + WARNING。这是 ARCHITECTURE「出站副作用：
                人批 + 留痕」的 stub，接批准通道时改这里，别在别处绕
      write     每次调用 INFO 留痕（名字 + 参数摘要 + 结果 ok 与否）
      read/none 只记 DEBUG
    """
    spec = registry.tools.get(name)
    if not spec or not spec.handler:
        return {"ok": False, "detail": f"unknown tool: {name}"}
    if name not in CHAT_TOOL_NAMES:
        return {"ok": False, "detail": f"tool not available: {name}"}

    if spec.side_effect == "outbound":
        log.warning(
            "tool %s is outbound and no approval channel exists; refused args=%s",
            name,
            _brief(arguments),
        )
        return {
            "ok": False,
            "detail": "outbound tools need human approval; not available in this build",
        }

    if spec.side_effect == "write":
        log.info("tool %s args=%s", name, _brief(arguments))
    else:
        log.debug("tool %s args=%s", name, _brief(arguments))

    try:
        result = spec.handler(**arguments)
        if asyncio.iscoroutine(result):
            result = await result
        out = result if isinstance(result, dict) else {"ok": True, "result": result}
    except TypeError as e:
        out = {"ok": False, "detail": f"bad arguments: {e}"}
    except Exception as e:  # noqa: BLE001 — surface to model
        out = {"ok": False, "detail": str(e)}

    if spec.side_effect == "write":
        log.info("tool %s -> ok=%s%s", name, out.get("ok"),
                 f" detail={out.get('detail')}" if not out.get("ok") else "")
    return out


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
    """一轮聊天。整轮持 turn 锁（app/locks.py）：同一用户串行，wake 等这轮说完。

    用户句在锁内落库（pending）——连发两条时第二条要等第一条整轮结束再插，
    库里的顺序才和回复的顺序一致。
    """
    uid = settings.user_id
    new_turn("chat")
    async with turn_lock(uid):
        row = await db.begin_user_turn(uid, user_text)
        return await _run_turn(uid, row)


async def retry_chat(user_message_id: int) -> dict[str, Any]:
    """重发一句 failed 的：同一行 failed → pending，再跑同一条轮次逻辑。不重插。"""
    uid = settings.user_id
    new_turn("chat")
    async with turn_lock(uid):
        got = await db.reopen_turn(user_message_id, uid)
        if not got.get("ok"):
            raise RetryRefused(str(got.get("detail") or "refused"))
        log.info("retrying user_message_id=%s", user_message_id)
        return await _run_turn(uid, got["message"])


async def _run_turn(uid: str, user_row: dict[str, Any]) -> dict[str, Any]:
    """user 行已是 pending。跑完：要么 complete_turn_tx（done + 回复一个事务），
    要么标 failed + reason 再抛 TurnFailed。不存在「落了 user 没人管」的第三种结局。"""
    started = time.monotonic()
    llm_calls = 0
    tool_calls_total = 0
    user_msg_id = int(user_row["id"])
    user_text = str(user_row["content"])

    # 只有 done 的轮次进历史；当前句是 pending，由 Trigger 单独带（不重复）。
    prior = await db.list_recent_turns(uid, limit=40)

    messages = build_messages(
        user_id=uid,
        history_rows=prior,
        recall=recall_text(uid),
        trigger=Trigger(kind="user", text=user_text),
    )

    tools = registry.openai_tools(CHAT_TOOL_NAMES)
    touched: list[str] = []
    wakes_touched: list[int] = []

    def _summary(outcome: str) -> None:
        log.info(
            "turn %s ms=%s llm_calls=%s tool_calls=%s history=%s",
            outcome,
            int((time.monotonic() - started) * 1000),
            llm_calls,
            tool_calls_total,
            len(prior),
        )

    async def _commit(reply: str, outcome: str) -> dict[str, Any]:
        assistant = await db.complete_turn_tx(user_msg_id, uid, reply)
        if assistant is None:
            # 锁保证不会发生：pending 只有这一轮在改。真到这儿就是有人绕过了锁。
            log.warning("user_message_id=%s not pending at commit; reply dropped", user_msg_id)
            _summary("not_pending")
            raise TurnFailed(user_msg_id, "not_pending", None)
        _summary(outcome)
        return await _finish(uid, assistant, touched, wakes_touched)

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            llm_calls += 1
            msg = await chat_completion(messages, tools=tools, tool_choice="auto")
            tool_calls = msg.get("tool_calls") or []

            if tool_calls:
                tool_calls_total += len(tool_calls)
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
            return await _commit(reply, "ok")

        # 工具轮次用尽：最后一次不给工具，逼他收口。这是有意的降级，要留痕。
        log.warning("tool rounds exhausted (%s); forcing a final reply without tools", MAX_TOOL_ROUNDS)
        llm_calls += 1
        msg = await chat_completion(messages, tools=None)
        reply = scrub_reply((msg.get("content") or "").strip()) or "（这轮工具次数用尽了，再说一次试试）"
        return await _commit(reply, "exhausted")

    except TurnFailed:
        raise
    except Exception as e:  # noqa: BLE001 — 任何没等到回复的结局都要在 user 行上留痕
        reason = f"llm_{e.status}" if isinstance(e, LLMError) else "error"
        try:
            await db.fail_turn(user_msg_id, reason)
        except Exception:  # noqa: BLE001
            log.warning("fail_turn(%s) itself failed", user_msg_id, exc_info=True)
        log.warning("turn failed user_message_id=%s reason=%s: %s", user_msg_id, reason, e)
        _summary("failed")
        raise TurnFailed(user_msg_id, reason, e) from e
