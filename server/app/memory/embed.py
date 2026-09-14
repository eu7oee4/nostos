"""向量服务客户端 + 每个用户一份向量缓存（docs/MIN_MEMORY.md「检索」）。

选型（2026-09-14 拍板，Notion「记忆系统设计对照」⑪）：先接本机 ombre-ollama 容器里的
bge-m3（1024 维）。一个 httpx 调用，不加依赖，不出网、不要 key。代价是容器常驻 2~3 GB 内存、
换模型要全量重算——所以缓存文件带 **签名**（api_format:model:dim），签名不同整份作废。

md 是唯一真源，向量只是缓存：`data/memories/<user>/vectors.json`，可以整个删掉重建。
**不放进 index.json**：那份人要读、要改，1024 个浮点数一条会把它糊成一坨。

哪条要重算看 **sha256**（Notion ②，EverOS cascade watcher 的核心规则）：hash 只盖 md 正文，
不盖 title / updated_at 这类审计字段。用户手改 `hometown.md` 把杭州改成上海，下次召回
读缓存时 sha 对不上就重嵌。不要 watchdog、不要队列。

服务挂了 / 没配 → `embed_texts` 回 None，召回退回关键词一路 + 新的在前，**不炸聊天**。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.memory.store import memories_dir

log = logging.getLogger("nostos.embed")

VECTORS_NAME = "vectors.json"
_EMBED_BATCH = 32


def enabled() -> bool:
    return bool((settings.embed_base_url or "").strip())


def content_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _signature(dim: int | None = None) -> str:
    return f"{settings.embed_api_format}:{settings.embed_model}:{dim or '?'}"


async def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """一批文本 → 一批向量。没配 / 挂了 / 形状不对都回 None，只记日志。"""
    if not enabled() or not texts:
        return None
    base = settings.embed_base_url.rstrip("/")
    fmt = (settings.embed_api_format or "ollama").lower()
    headers = {"Content-Type": "application/json"}
    if settings.embed_api_key:
        headers["Authorization"] = f"Bearer {settings.embed_api_key}"
    out: list[list[float]] = []
    try:
        async with httpx.AsyncClient(timeout=settings.embed_timeout_seconds) as client:
            for i in range(0, len(texts), _EMBED_BATCH):
                chunk = texts[i : i + _EMBED_BATCH]
                if fmt == "openai":
                    resp = await client.post(
                        f"{base}/embeddings",
                        json={"model": settings.embed_model, "input": chunk},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    rows = sorted(data["data"], key=lambda r: r.get("index", 0))
                    vecs = [r["embedding"] for r in rows]
                else:
                    resp = await client.post(
                        f"{base}/api/embed",
                        json={"model": settings.embed_model, "input": chunk},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    vecs = resp.json()["embeddings"]
                if len(vecs) != len(chunk):
                    raise ValueError(f"got {len(vecs)} vectors for {len(chunk)} inputs")
                out.extend([list(map(float, v)) for v in vecs])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("embed failed (%s %s): %s", fmt, settings.embed_model, e)
        return None
    return out


# --- 向量缓存 ---------------------------------------------------------------


def _vectors_path(user_id: str | None = None) -> Path:
    return memories_dir(user_id) / VECTORS_NAME


def load_vectors(user_id: str | None = None) -> dict[str, Any]:
    """{"signature": str, "vectors": {id: {"sha256": str, "vector": [...]}}}。坏文件当空。"""
    path = _vectors_path(user_id)
    if not path.is_file():
        return {"signature": None, "vectors": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("vectors"), dict):
            raise ValueError("bad shape")
        return {"signature": data.get("signature"), "vectors": data["vectors"]}
    except (json.JSONDecodeError, OSError, ValueError):
        log.warning("vectors.json unreadable, rebuilding: %s", path)
        return {"signature": None, "vectors": {}}


def _save_vectors(data: dict[str, Any], user_id: str | None = None) -> None:
    path = _vectors_path(user_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


async def ensure_vectors(
    items: list[dict[str, Any]],
    texts: dict[str, str],
    user_id: str | None = None,
) -> dict[str, list[float]] | None:
    """给 `items`（list_memories 的输出）返回 {id: vector}，缺的 / sha 变了的现算写回。

    `texts` 是 {id: 拿去嵌的文本}。签名不一致（换了模型）整份作废重算。
    删掉的记忆顺手从缓存里清掉。服务不可用回 None（已有的缓存不动）。
    """
    if not enabled():
        return None
    cache = load_vectors(user_id)
    vectors: dict[str, Any] = dict(cache.get("vectors") or {})
    sig_known = cache.get("signature")
    if sig_known and not sig_known.startswith(f"{settings.embed_api_format}:{settings.embed_model}:"):
        log.info("embedding signature changed (%s -> %s*), recomputing all", sig_known, _signature())
        vectors = {}

    wanted = {m["id"] for m in items}
    stale = [k for k in vectors if k not in wanted]
    for k in stale:
        vectors.pop(k, None)

    todo: list[str] = []
    shas: dict[str, str] = {}
    for m in items:
        mid = m["id"]
        sha = content_sha256(texts.get(mid, ""))
        shas[mid] = sha
        got = vectors.get(mid)
        if not got or got.get("sha256") != sha or not got.get("vector"):
            todo.append(mid)

    if todo:
        vecs = await embed_texts([texts.get(mid, "") for mid in todo])
        if vecs is None:
            # 服务挂了：已有的还能用，缺的这轮先没有
            if stale:
                _save_vectors({"signature": sig_known, "vectors": vectors}, user_id)
            return {k: v["vector"] for k, v in vectors.items() if v.get("vector")} or None
        for mid, vec in zip(todo, vecs):
            vectors[mid] = {"sha256": shas[mid], "vector": vec}
        dim = len(vecs[0]) if vecs else None
        _save_vectors({"signature": _signature(dim), "vectors": vectors}, user_id)
        log.info("embedded %s memories (%s stale removed)", len(todo), len(stale))
    elif stale:
        _save_vectors({"signature": sig_known, "vectors": vectors}, user_id)

    return {k: v["vector"] for k, v in vectors.items() if v.get("vector")}


def forget_vector(mem_id: str, user_id: str | None = None) -> None:
    """删记忆时顺手删缓存。不删也没事，下次 ensure_vectors 会清。"""
    cache = load_vectors(user_id)
    if mem_id in cache["vectors"]:
        cache["vectors"].pop(mem_id, None)
        _save_vectors(cache, user_id)
