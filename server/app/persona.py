"""Companion persona markdown: data/persona.md."""

from __future__ import annotations

from pathlib import Path

from app.config import settings

PERSONA_NAME = "persona.md"


def persona_path() -> Path:
    return Path(settings.data_dir) / PERSONA_NAME


def load_persona() -> str | None:
    """Return persona markdown, or None if missing/blank."""
    path = persona_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None
