"""File-backed memories: data/memories/<user_id>/*.md + index.json.

md 是唯一真源；index.json 只记元数据（kind / intensity / updated_at），整个删掉
重建也不丢记忆。

两种 kind（2026-09-14 定，Notion「记忆系统设计对照」第 ⑫ 点）：

    item   事件 / 短事实：「妈妈 9 月中旬来杭州小住」
    feel   感受，带 intensity（low / mid / high）：「和妈妈长时间相处会紧张」

kind 在写入端就定了——模型选 memory_write_item 还是 memory_write_feel 就是在选
kind。老记忆文件（index 里没 kind 的）一律按 item。

召回按 updated_at **倒序**，每条带记下的日期（第 ① 点）：检索上线前这是唯一
排序规则，上线后作为同分时的次序。原来按文件名字母序，z 开头的记忆永远最先被
截掉，而且模型完全不知道一条是什么时候记的。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings

log = logging.getLogger("nostos.memory")

INDEX_NAME = "index.json"
MAX_RECALL_CHARS = 6000
EMPTY_RECALL = "（暂无长期记忆）"
KINDS = ("item", "feel")
INTENSITIES = ("low", "mid", "high")
_INTENSITY_ZH = {"low": "淡", "mid": "中", "high": "浓"}
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
    if "�" in text:
        return True
    # Dense CJK Compatibility / rare private-use-ish junk often appears in mojibake titles
    weird = sum(1 for ch in text if ord(ch) >= 0xE000 or "" <= ch <= "ÿ")
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


def _norm_kind(kind: Any) -> str:
    return kind if kind in KINDS else "item"


def _norm_intensity(kind: str, intensity: Any) -> str | None:
    """feel 才有浓度；index 里的值不认识就按 mid；item 一律 None。"""
    if kind != "feel":
        return None
    return intensity if intensity in INTENSITIES else "mid"


def _upsert_index_entry(
    mem_id: str,
    title: str,
    user_id: str | None = None,
    *,
    kind: str = "item",
    intensity: str | None = None,
) -> None:
    data = _load_index(user_id)
    items: list[dict[str, Any]] = list(data.get("memories") or [])
    entry = {
        "id": mem_id,
        "title": title,
        "kind": kind,
        "intensity": intensity,
        "updated_at": _now(),
    }
    for i, item in enumerate(items):
        if item.get("id") == mem_id:
            items[i] = {**item, **entry}
            break
    else:
        items.append(entry)
    data["memories"] = items
    _save_index(data, user_id)


def _remove_index_entry(mem_id: str, user_id: str | None = None) -> None:
    data = _load_index(user_id)
    data["memories"] = [
        m for m in (data.get("memories") or []) if m.get("id") != mem_id
    ]
    _save_index(data, user_id)


def list_memories(user_id: str | None = None) -> list[dict[str, Any]]:
    """List memory metadata, **newest first** (updated_at desc; 没索引的按 mtime).

    title prefers md H1 over index (avoids tool-arg mojibake). index 里没 kind 的
    老文件按 item。
    """
    uid = user_id or settings.user_id
    root = memories_dir(uid)
    indexed = {m["id"]: m for m in _load_index(uid).get("memories", []) if m.get("id")}
    out: list[dict[str, Any]] = []
    for path in root.glob("*.md"):
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
        kind = _norm_kind(meta.get("kind"))
        out.append(
            {
                "id": mem_id,
                "title": title,
                "kind": kind,
                "intensity": _norm_intensity(kind, meta.get("intensity")),
                "updated_at": updated,
                "path": str(path.relative_to(Path(settings.data_dir))),
            }
        )
    # 新的在前；updated_at 缺失的排最后；同一时刻按 id 稳定
    out.sort(key=lambda m: (m["updated_at"] or "", m["id"]), reverse=True)
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
        "kind": meta.get("kind", "item"),
        "intensity": meta.get("intensity"),
        "content": content,
        "updated_at": meta.get("updated_at"),
    }


def write_memory(
    name: str,
    content: str,
    title: str | None = None,
    user_id: str | None = None,
    *,
    kind: str = "item",
    intensity: str | None = None,
) -> dict[str, Any]:
    """写一条。kind 由调用方（工具）定；feel 必须带合法 intensity，item 忽略它。"""
    uid = user_id or settings.user_id
    mem_id = safe_id(name)
    body = (content or "").strip()
    if not body:
        return {"ok": False, "id": mem_id, "detail": "empty content"}
    if kind not in KINDS:
        return {"ok": False, "id": mem_id, "detail": f"kind must be one of {list(KINDS)}"}
    if kind == "feel" and intensity not in INTENSITIES:
        return {
            "ok": False,
            "id": mem_id,
            "detail": f"intensity must be one of {list(INTENSITIES)}",
        }
    intensity = _norm_intensity(kind, intensity)

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
    _upsert_index_entry(mem_id, display, uid, kind=kind, intensity=intensity)
    log.info("memory written kind=%s intensity=%s id=%s", kind, intensity, mem_id)
    return {
        "ok": True,
        "id": mem_id,
        "title": display,
        "kind": kind,
        "intensity": intensity,
        "path": str(path.relative_to(Path(settings.data_dir))),
        "bytes": len(body.encode("utf-8")),
    }


def delete_memory(name: str, user_id: str | None = None) -> bool:
    """隐私出口用（PLAN §4.2「用户可看可删」）。**模型没有这个入口**——和 prefs 不给
    delete 是同一条纪律。md 和 index 条目一起删。"""
    uid = user_id or settings.user_id
    mem_id = safe_id(name)
    path = memories_dir(uid) / f"{mem_id}.md"
    existed = path.is_file()
    if existed:
        path.unlink()
    _remove_index_entry(mem_id, uid)
    if existed:
        from app.memory.embed import forget_vector  # 延迟导入：embed 依赖这里的 memories_dir

        forget_vector(mem_id, uid)
        log.info("memory deleted id=%s", mem_id)
    return existed


def _recall_date(updated_at: str | None, now: datetime) -> str | None:
    """记忆头上的日期。同一年只写 MM-DD（和对话戳一个口径，年份逐条重复零信息）；
    跨年才带年份——「去年 9 月」和「今年 9 月」对模型不是一回事。"""
    if not updated_at:
        return None
    try:
        dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(settings.timezone)
    local = dt.astimezone(tz)
    if local.year == now.astimezone(tz).year:
        return local.strftime("%m-%d")
    return local.strftime("%Y-%m-%d")


def _recall_header(meta: dict[str, Any], title: str, now: datetime) -> str:
    parts = [f"### {title} (`{meta['id']}`)"]
    if meta.get("kind") == "feel":
        level = _INTENSITY_ZH.get(meta.get("intensity") or "mid", "中")
        parts.append(f"感受，浓度{level}")
    date = _recall_date(meta.get("updated_at"), now)
    if date:
        parts.append(f"记于 {date}")
    return " | ".join(parts)


def render_recall_block(meta: dict[str, Any], title: str, content: str, now: datetime) -> str:
    """一条记忆在召回块里的样子：带日期的标题 + 正文。recall.py 和这里的全量版共用。"""
    return f"{_recall_header(meta, title, now)}\n{content.strip()}\n"


def recall_text(
    user_id: str | None = None,
    max_chars: int = MAX_RECALL_CHARS,
    *,
    now: datetime | None = None,
) -> str:
    """全量版召回：新的在前，每条标题带「记于 MM-DD」；feel 还带浓度。预算截断从最旧的那头砍。

    聊天 / wake 走的是 `recall.recall()`（关键词 + 向量 + RRF）；这份是它的退化形状，
    也是测试和 `/memories` 之外看「模型眼里的记忆长什么样」的入口。
    """
    uid = user_id or settings.user_id
    items = list_memories(uid)
    if not items:
        return EMPTY_RECALL
    at = now or datetime.now(timezone.utc)

    chunks: list[str] = []
    used = 0
    for meta in items:
        got = read_memory(meta["id"], uid)
        if not got.get("ok"):
            continue
        block = render_recall_block(meta, got.get("title") or meta["id"], got["content"], at)
        if used + len(block) > max_chars:
            remain = max_chars - used
            if remain > 80:
                chunks.append(block[:remain] + "\n…(截断)")
            break
        chunks.append(block)
        used += len(block)

    if not chunks:
        return EMPTY_RECALL
    return "\n".join(chunks)
