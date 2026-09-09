"""User profile on disk: data/user_profile.json (no timezone)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings

PROFILE_NAME = "user_profile.json"


def profile_path() -> Path:
    return Path(settings.data_dir) / PROFILE_NAME


def _raw_profile() -> dict[str, Any]:
    path = profile_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def nickname() -> str | None:
    """引导真的写下来的称呼；没写过返回 None。

    不拿 USER_ID 兜底：那是运维标识（默认就是 `local`），当着模型的面把它当
    称呼用，等于每轮告诉他"这人叫 local"。拼装侧宁可整行不写。
    """
    return (_raw_profile().get("nickname") or "").strip() or None


def load_profile() -> dict[str, Any]:
    """Load profile dict. Always includes nickname (default: USER_ID or 你)."""
    path = profile_path()
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                raw = data
        except (json.JSONDecodeError, OSError):
            raw = {}

    nickname = (raw.get("nickname") or "").strip() or (settings.user_id or "你")
    out: dict[str, Any] = {"nickname": nickname}

    gender = (raw.get("gender") or "").strip()
    if gender:
        out["gender"] = gender

    companion_gender = (raw.get("companion_gender") or "").strip()
    if companion_gender:
        out["companion_gender"] = companion_gender

    return out


def profile_block() -> str | None:
    """System block for assembly. Missing/empty file → None (omit)."""
    path = profile_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not data:
        return None

    # Only emit if at least one identity field is present in the file.
    has_field = any(
        (data.get(k) or "").strip()
        for k in ("nickname", "gender", "companion_gender")
    )
    if not has_field:
        return None

    p = load_profile()
    lines = ["【用户档案】"]
    nick = nickname()
    if nick:
        lines.append(f"称呼：{nick}")
    if p.get("gender"):
        lines.append(f"对方性别气质：{p['gender']}")
    if p.get("companion_gender"):
        lines.append(f"伙伴称谓：{p['companion_gender']}")
    return "\n".join(lines)
