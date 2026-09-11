from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.chat_loop import run_chat
from app.config import settings
from app import db
from app.llm import LLMError
from app.memory import list_memories, read_memory
from app.prefs import delete_style, list_style, load_wake, save_wake
from app.push import public_key_b64u
from app.schedule.policy import random_on
from app.schedule.scheduler import reload_wake_policy, schedule_wake

router = APIRouter()


class ChatIn(BaseModel):
    content: str = Field(min_length=1, max_length=16000)


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=512)
    auth: str = Field(min_length=1, max_length=256)


class PushSubIn(BaseModel):
    """浏览器 `pushManager.subscribe()` 返回的 subscription 原样上报。"""

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeys


class WakeIn(BaseModel):
    delay_seconds: float | None = Field(default=None, ge=0)
    wake_at: str | None = None
    note: str | None = None
    intent: str = "check_in"


async def _random_wake_status() -> dict[str, Any]:
    """随机醒来现在是什么状态：开没开、下一次几点、护栏是哪几条。

    `next_at` 是空 = 现在没武装。开着却一直是空，看 `nostos.wake` 日志里
    `auto wake not armed` / `no_slot` 那行——多半是护栏把窗口掐没了。
    """
    pending = await db.list_pending_auto_wakes(settings.user_id)
    on, reason = random_on()
    return {
        "enabled": on,
        "detail": reason,
        "next_at": pending[0]["wake_at"] if pending else None,
        "guardrails": load_wake(),
    }


@router.get("/health")
async def health():
    return {
        "ok": True,
        "service": "nostos",
        "stage": "min-wake",
        "user_id": settings.user_id,
        "model": settings.llm_model,
        "has_key": bool(settings.llm_api_key),
        "memory_count": len(list_memories(settings.user_id)),
        "proactive_enabled": settings.proactive_enabled,
        "push_subscriptions": await db.count_push_subscriptions(settings.user_id),
        "pending_wakes": await db.count_pending_wakes(settings.user_id),
        "random_wake": await _random_wake_status(),
    }


@router.get("/messages")
async def get_messages(limit: int = 100):
    limit = max(1, min(limit, 500))
    return {"user_id": settings.user_id, "messages": await db.list_messages(settings.user_id, limit)}


@router.get("/memories")
def get_memories():
    """List durable memories (markdown files on disk)."""
    return {"user_id": settings.user_id, "memories": list_memories(settings.user_id)}


@router.get("/memories/{name}")
def get_memory(name: str):
    got = read_memory(name, settings.user_id)
    if not got.get("ok"):
        raise HTTPException(404, got.get("detail") or "not found")
    return got


@router.get("/prefs")
def get_prefs():
    """纠偏偏好的隐私出口（PLAN §4.2「关于用户的事实，用户可看可删」）。

    对话流里一个字都不露（模型记完不宣布），能看见它们的地方只有这儿和菜单。
    """
    return {"user_id": settings.user_id, "prefs": list_style()}


@router.delete("/prefs/{pref_id}")
def delete_pref(pref_id: str):
    if not delete_style(pref_id):
        raise HTTPException(404, "not found")
    return {"ok": True, "id": pref_id}


@router.get("/prefs/wake")
def get_wake_prefs():
    """随机醒来的护栏。**模型没有这个入口**——它不能自己把安静时段放宽。"""
    return {"user_id": settings.user_id, "wake": load_wake()}


@router.put("/prefs/wake")
async def put_wake_prefs(body: dict[str, Any]):
    """改护栏。**合并写入**：只带 `{"daily_cap":{"max":1}}` 时其他三条不动。

    存下来之后立刻重算下一次随机醒来——不然改完得等到下一次聊天或重启才生效，
    而「我把安静时段调宽了他今晚还是不来」这种是查不出来的。
    """
    wake = save_wake(body)
    return {"ok": True, "wake": wake, "auto_wake": await reload_wake_policy()}


@router.get("/push/vapid")
def push_vapid():
    """前端 subscribe() 要的 applicationServerKey。第一次调用会生成密钥对。"""
    return {"public_key": public_key_b64u()}


@router.post("/push/subscribe")
async def push_subscribe(body: PushSubIn):
    """存一台设备的订阅。同 endpoint 重复上报是覆盖，不是新增。"""
    row = await db.upsert_push_subscription(
        settings.user_id, body.endpoint, body.keys.p256dh, body.keys.auth
    )
    return {"ok": True, "id": row.get("id")}


@router.delete("/push/subscribe")
async def push_unsubscribe(endpoint: str):
    """用户在这台设备上关掉通知。前端应同时调 subscription.unsubscribe()。"""
    ok = await db.delete_push_subscription(endpoint)
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True}


@router.get("/wakes")
async def get_wakes(status: str | None = "pending"):
    return {
        "user_id": settings.user_id,
        "proactive_enabled": settings.proactive_enabled,
        "wakes": await db.list_wakes(settings.user_id, status=status),
    }


@router.post("/wakes")
async def post_wake(body: WakeIn):
    """Arm a one-shot wake (local test without chatting)."""
    result = await schedule_wake(
        delay_seconds=body.delay_seconds,
        wake_at=body.wake_at,
        note=body.note,
        intent=body.intent,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("detail") or "wake failed")
    return result


@router.post("/chat")
async def chat(body: ChatIn):
    text = body.content.strip()
    if not text:
        raise HTTPException(400, "empty content")

    try:
        return await run_chat(text)
    except LLMError as e:
        code = 502
        if e.status == 401:
            code = 503
        elif 400 <= e.status < 500:
            code = e.status
        return JSONResponse({"detail": str(e), "status": e.status}, status_code=code)
