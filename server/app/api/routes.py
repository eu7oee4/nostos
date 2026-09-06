from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.chat_loop import run_chat
from app.config import settings
from app import db
from app.llm import LLMError
from app.memory import list_memories, read_memory

router = APIRouter()


class ChatIn(BaseModel):
    content: str = Field(min_length=1, max_length=16000)


@router.get("/health")
def health():
    return {
        "ok": True,
        "service": "nostos",
        "stage": "min-memory",
        "user_id": settings.user_id,
        "model": settings.llm_model,
        "has_key": bool(settings.llm_api_key),
        "memory_count": len(list_memories(settings.user_id)),
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
