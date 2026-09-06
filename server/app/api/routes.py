from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import settings
from app import db
from app.llm import LLMError, chat_completion

router = APIRouter()


class ChatIn(BaseModel):
    content: str = Field(min_length=1, max_length=16000)


@router.get("/health")
def health():
    return {
        "ok": True,
        "service": "nostos",
        "stage": "min-chat",
        "user_id": settings.user_id,
        "model": settings.llm_model,
        "has_key": bool(settings.llm_api_key),
    }


@router.get("/messages")
async def get_messages(limit: int = 100):
    limit = max(1, min(limit, 500))
    return {"user_id": settings.user_id, "messages": await db.list_messages(settings.user_id, limit)}


@router.post("/chat")
async def chat(body: ChatIn):
    text = body.content.strip()
    if not text:
        raise HTTPException(400, "empty content")

    await db.add_message(settings.user_id, "user", text)
    history = await db.history_for_llm(settings.user_id, limit=40)

    # Minimal system: companion framing, no tools/memory yet
    messages = [
        {
            "role": "system",
            "content": (
                "你是 nostos，用户的 AI 伙伴（不是助手工具箱）。"
                "说话自然、简短；这是最小可聊版本，还没有长期记忆与主动触达。"
            ),
        },
        *history,
    ]

    try:
        reply = await chat_completion(messages)
    except LLMError as e:
        code = 502
        if e.status == 401:
            code = 503
        elif 400 <= e.status < 500:
            code = e.status
        return JSONResponse({"detail": str(e), "status": e.status}, status_code=code)

    assistant = await db.add_message(settings.user_id, "assistant", reply or "")
    return {"user_id": settings.user_id, "message": assistant}
