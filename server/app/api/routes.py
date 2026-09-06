from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import db
from app.chat_loop import run_chat
from app.config import settings
from app.llm import LLMError
from app.memory import list_memories, read_memory
from app.schedule.prefs import load_prefs, save_prefs
from app.schedule.scheduler import reload_wake_policy, schedule_wake

router = APIRouter()


class ChatIn(BaseModel):
    content: str = Field(min_length=1, max_length=16000)


class WakeIn(BaseModel):
    delay_seconds: float | None = Field(default=None, ge=0)
    wake_at: str | None = None
    note: str | None = None
    intent: str = "check_in"


class QuietWindow(BaseModel):
    start: str
    end: str


class RuleToggleMinutes(BaseModel):
    enabled: bool = True
    minutes: int | None = None


class RuleToggleMax(BaseModel):
    enabled: bool = True
    max: int | None = None


class QuietHoursIn(BaseModel):
    enabled: bool = True
    windows: list[QuietWindow] | None = None


class RandomIn(BaseModel):
    enabled: bool = True
    max_horizon_hours: int | None = None


class WakePrefsIn(BaseModel):
    timezone: str | None = None
    proactive_enabled: bool | None = None
    quiet_hours: QuietHoursIn | None = None
    min_interval: RuleToggleMinutes | None = None
    daily_cap: RuleToggleMax | None = None
    recent_chat: RuleToggleMinutes | None = None
    random: RandomIn | None = None
    policy_patrol_minutes: int | None = None


@router.get("/health")
async def health():
    prefs = load_prefs()
    return {
        "ok": True,
        "service": "nostos",
        "stage": "random-wake",
        "user_id": settings.user_id,
        "model": settings.llm_model,
        "has_key": bool(settings.llm_api_key),
        "memory_count": len(list_memories(settings.user_id)),
        "proactive_enabled": bool(prefs.get("proactive_enabled")),
        "wake_prefs": {
            "quiet_hours": prefs.get("quiet_hours"),
            "min_interval": prefs.get("min_interval"),
            "daily_cap": prefs.get("daily_cap"),
            "recent_chat": prefs.get("recent_chat"),
            "random": prefs.get("random"),
            "policy_patrol_minutes": prefs.get("policy_patrol_minutes"),
        },
        "pending_wakes": await db.count_pending_wakes(settings.user_id),
    }


@router.get("/settings/wake")
async def get_wake_settings():
    prefs = load_prefs()
    return {"user_id": settings.user_id, "prefs": prefs}


@router.put("/settings/wake")
async def put_wake_settings(body: WakePrefsIn):
    current = load_prefs()
    patch: dict[str, Any] = body.model_dump(exclude_unset=True)
    # deep-merge nested rule objects onto current
    for key in ("quiet_hours", "min_interval", "daily_cap", "recent_chat", "random"):
        if key in patch and isinstance(patch[key], dict):
            merged = dict(current.get(key) or {})
            for k, v in patch[key].items():
                if v is not None:
                    merged[k] = v
            patch[key] = merged
    merged_all = {**current, **patch}
    saved = save_prefs(merged_all)
    reload_result = await reload_wake_policy()
    return {
        "ok": True,
        "user_id": settings.user_id,
        "prefs": saved,
        "reload": reload_result,
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
    prefs = load_prefs()
    return {
        "user_id": settings.user_id,
        "proactive_enabled": bool(prefs.get("proactive_enabled")),
        "wakes": await db.list_wakes(settings.user_id, status=status),
    }


@router.post("/wakes")
async def post_wake(body: WakeIn):
    """Arm a one-shot wake (manual test; not bound by random policy)."""
    result = await schedule_wake(
        delay_seconds=body.delay_seconds,
        wake_at=body.wake_at,
        note=body.note,
        intent=body.intent,
        source="manual",
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
