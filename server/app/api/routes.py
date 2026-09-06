from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.chat_loop import run_chat
from app.config import settings
from app import db
from app.llm import LLMError
from app.memory import list_memories, read_memory
from app.schedule.scheduler import schedule_wake

router = APIRouter()


class ChatIn(BaseModel):
    content: str = Field(min_length=1, max_length=16000)


class WakeIn(BaseModel):
    delay_seconds: float | None = Field(default=None, ge=0)
    wake_at: str | None = None
    note: str | None = None
    intent: str = "check_in"


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
        "pending_wakes": await db.count_pending_wakes(settings.user_id),
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
