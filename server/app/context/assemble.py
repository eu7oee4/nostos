"""Single prompt assembly pipeline (cache-disciplined order)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.config import settings
from app.context.system_prompt import get_system_prompt
from app.persona import load_persona
from app.profile import load_profile, profile_block

_WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_EMPTY_RECALL = "（暂无长期记忆）"


@dataclass(frozen=True)
class Trigger:
    """Current-turn trigger (not part of frozen dialog history)."""

    kind: Literal["user", "wake"]
    text: str = ""
    note: str | None = None
    intent: str | None = None


def _zone(tz_name: str | None = None) -> ZoneInfo:
    return ZoneInfo(tz_name or settings.timezone)


def _parse_created_at(raw: str | datetime | None) -> datetime:
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    if not raw:
        return datetime.now(timezone.utc)
    s = str(raw).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_message_stamp(created_at: str | datetime | None, tz_name: str | None = None) -> str:
    """Stable frozen stamp: MM-DD HH:MM in configured timezone."""
    tz = _zone(tz_name)
    dt = _parse_created_at(created_at).astimezone(tz)
    return dt.strftime("%m-%d %H:%M")


def format_time_anchor(
    now: datetime | None = None,
    tz_name: str | None = None,
) -> str:
    """Per-turn suffix: 此刻：YYYY-MM-DD weekday HH:MM (tz)."""
    name = tz_name or settings.timezone
    tz = _zone(name)
    dt = (now or datetime.now(timezone.utc)).astimezone(tz)
    wd = _WEEKDAYS_ZH[dt.weekday()]
    return f"此刻：{dt.strftime('%Y-%m-%d')} {wd} {dt.strftime('%H:%M')} ({name})"


def _render_dialog_row(row: dict[str, Any], tz_name: str) -> dict[str, str] | None:
    role = row.get("role")
    if role not in ("user", "assistant"):
        return None
    content = row.get("content") or ""
    stamp = format_message_stamp(row.get("created_at"), tz_name)
    return {"role": role, "content": f"[{stamp}] {content}"}


def _trigger_message(trigger: Trigger, nickname: str) -> dict[str, str]:
    if trigger.kind == "user":
        body = trigger.text or ""
        return {
            "role": "user",
            "content": f「{nickname}发来一条消息：{body}」,
        }

    # wake (7b): same pipeline; reusable when wake fire adopts assemble.
    parts = [f"你醒了，{nickname}没找你。"]
    note = (trigger.note or "").strip()
    intent = (trigger.intent or "").strip()
    extra = (trigger.text or "").strip()
    if note:
        parts.append(f"预约备注：{note}")
    elif intent:
        parts.append(f"意图：{intent}")
    if extra and extra not in (note, intent):
        parts.append(extra)
    return {"role": "user", "content": "\n".join(parts)}


def build_messages(
    *,
    user_id: str,
    history_rows: list[dict[str, Any]],
    recall: str,
    trigger: Trigger,
) -> list[dict[str, Any]]:
    """Assemble LLM messages in cache-stable order.

    history_rows = prior turns only (id/role/content/created_at);
    current user/wake text comes from trigger (no double user line).
    user_id is reserved for future per-user assets; profile/persona are
    single-tenant paths under data_dir today.
    """
    _ = user_id  # single-tenant for now; keep signature for wake/multi-user
    tz_name = settings.timezone
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": get_system_prompt()},
    ]

    block = profile_block()
    if block:
        messages.append({"role": "system", "content": block})

    persona = load_persona()
    if persona:
        messages.append(
            {
                "role": "system",
                "content": f"【伙伴人格 / persona】\n{persona}",
            }
        )

    for row in history_rows:
        rendered = _render_dialog_row(row, tz_name)
        if rendered:
            messages.append(rendered)

    recall_clean = (recall or "").strip()
    if recall_clean and recall_clean != _EMPTY_RECALL:
        messages.append(
            {
                "role": "system",
                "content": f"现在浮现在你脑海里的记忆有：\n{recall_clean}",
            }
        )

    messages.append(
        {"role": "system", "content": format_time_anchor(tz_name=tz_name)}
    )

    nickname = load_profile().get("nickname") or settings.user_id or "你"
    messages.append(_trigger_message(trigger, nickname))
    return messages
