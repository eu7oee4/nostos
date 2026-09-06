"""File-backed memories: data/memories/<user_id>/*.md + index.json."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

INDEX_NAME = "index.json"
MAX_RECALL_CHARS = 6000
_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def memories_dir(user_id: str | None = None) -> Path:
    uid = user_id or settings.user_id
    path = Path(settings.data_dir) / "memories" / uid
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_id(name: str) -> str:
    raw = (name or "").strip()
    s = re.sub(r"[^\w\-]+", "-", raw, flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-_")[:80]
    return s or "note"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _index_path(user_id: str | None = None) -> Path:
    return memories_dir(user_id) / INDEX_NAME


def _looks_mojibake(text: str) -> bool:
    """Heuristic: replacement chars or common UTF-8-as-Latin1 garbage."""
    if not text:
        return True
    if "\ufffd" in text:
        return True
    # Dense CJK Compatibility / rare private-use-ish junk often appears in mojibake titles
    weird = sum(1 for ch in text if ord(ch) >= 0xE000 or "\u0080" <= ch <= "\u00ff")
    return weird >= max(2, len(text) // 3)


def _title_from_body(body: str, fallback: str) -> str:
    m = _H1.search(body or "")
    if m:
        t = m.group(1).strip()
        if t and not _looks_mojibake(t):
            return t[:80]
    return fallback


def _load_index(user_id: str | None = None) -> dict[str, Any]:
    path = _index_path(user_id)
    if not path.is_file():
        return {"memories": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"memories": []}
        data.setdefault("memories", [])
        return data
    except (json.JSONDecodeError, OSError):
        return {"memories": []}


def _save_index(data: dict[str, Any], user_id: str | None = None) -> None:
    path = _index_path(user_id)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _upsert_index_entry(
    mem_id: str,
    title: str,
    user_id: str | None = None,
) -> None:
    data = _load_index(user_id)
    items: list[dict[str, Any]] = list(data.get("memories") or [])
    now = _now()
    found = False
    for item in items:
        if item.get("id") == mem_id:
            item["title"] = title
            item["updated_at"] = now
            found = True
            break
    if not found:
        items.append({"id": mem_id, "title": title, "updated_at": now})
    data["memories"] = items
    _save_index(data, user_id)


def list_memories(user_id: str | None = None) -> list[dict[str, Any]]:
    """List memory metadata; title prefers md H1 over index (avoids tool-arg mojibake)."""
    uid = user_id or settings.user_id
    root = memories_dir(uid)
    indexed = {m["id"]: m for m in _load_index(uid).get("memories", []) if m.get("id")}
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.md")):
        mem_id = path.stem
        meta = indexed.get(mem_id, {})
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            body = ""
        indexed_title = meta.get("title") or ""
        fallback = mem_id if _looks_mojibake(indexed_title) else (indexed_title or mem_id)
        title = _title_from_body(body, fallback)
        updated = meta.get("updated_at")
        if not updated:
            try:
                updated = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            except OSError:
                updated = None
        out.append(
            {
                "id": mem_id,
                "title": title,
                "updated_at": updated,
                "path": str(path.relative_to(Path(settings.data_dir))),
            }
        )
    return out


def read_memory(name: str, user_id: str | None = None) -> dict[str, Any]:
    uid = user_id or settings.user_id
    mem_id = safe_id(name)
    path = memories_dir(uid) / f"{mem_id}.md"
    if not path.is_file():
        return {"ok": False, "id": mem_id, "detail": "not found"}
    content = path.read_text(encoding="utf-8")
    meta = next((m for m in list_memories(uid) if m["id"] == mem_id), {})
    return {
        "ok": True,
        "id": mem_id,
        "title": meta.get("title") or mem_id,
        "content": content,
        "updated_at": meta.get("updated_at"),
    }


def write_memory(
    name: str,
    content: str,
    title: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    uid = user_id or settings.user_id
    mem_id = safe_id(name)
    body = (content or "").strip()
    if not body:
        return {"ok": False, "id": mem_id, "detail": "empty content"}

    # Prefer H1 inside content; then clean title arg; else use id (never store mojibake titles)
    from_body = _title_from_body(body, "")
    if from_body:
        display = from_body
    elif title and not _looks_mojibake(title):
        display = title.strip()[:80]
    else:
        display = mem_id

    if not body.lstrip().startswith("#"):
        body = f"# {display}\n\n{body}\n"
    else:
        # Rewrite bad H1 if it looks like mojibake
        m = _H1.search(body)
        if m and _looks_mojibake(m.group(1)):
            body = _H1.sub(f"# {display}", body, count=1)
        body = body if body.endswith("\n") else body + "\n"

    path = memories_dir(uid) / f"{mem_id}.md"
    path.write_text(body, encoding="utf-8")
    display = _title_from_body(body, display)
    _upsert_index_entry(mem_id, display, uid)
    return {
        "ok": True,
        "id": mem_id,
        "title": display,
        "path": str(path.relative_to(Path(settings.data_dir))),
        "bytes": len(body.encode("utf-8")),
    }


def recall_text(user_id: str | None = None, max_chars: int = MAX_RECALL_CHARS) -> str:
    """Concatenate memories for prompt injection (召回挂当轮尾)."""
    uid = user_id or settings.user_id
    items = list_memories(uid)
    if not items:
        return "（暂无长期记忆）"

    chunks: list[str] = []
    used = 0
    for meta in items:
        got = read_memory(meta["id"], uid)
        if not got.get("ok"):
            continue
        block = f"### {got.get('title') or meta['id']} (`{meta['id']}`)\n{got['content'].strip()}\n"
        if used + len(block) > max_chars:
            remain = max_chars - used
            if remain > 80:
                chunks.append(block[:remain] + "\n…(截断)")
            break
        chunks.append(block)
        used += len(block)

    if not chunks:
        return "（暂无长期记忆）"
    return "\n".join(chunks)
