"""每轮一次的召回（Notion「记忆系统设计对照」一.3 / ⑤⑥⑦⑧）。

    query = 当前消息 + 最近 3 轮（上限 1800 字符，超了保头尾）
    两路：关键词（中文二元组）一路、向量一路，各取前 N
    RRF 融合 → 取前 K → 按字符预算截断
    每条带日期；同分按新的在前
    每轮一行日志：候选数、选中的 id、最高分、耗时

为什么 query 不只用当前一句：「今天去接她了」单句很弱，但最近 3 轮若聊到妈妈，向量路
就能捞到 09-03 那条「妈妈 9 月中旬来杭州小住」，模型才知道「她」是谁。

为什么不上重排：EverOS 实测 cross-encoder 重排是负收益。RRF 就是两家（蛋壳 1/(40+r)、
EverOS 1/(60+r)）都在用的那个公式，不发明新的。

退化：没配向量服务或它挂了 → 只剩关键词一路；关键词也没命中的按新的在前补到预算。
所以记忆少的时候（≤ K 条）行为和「全量塞入、新的在前」一样，只是命中的排前面。
记忆多起来才有真的取舍——这正是这一步排在第 3 的理由。

查重（⑧）不在这儿：`write_memory` 同 id 覆盖看的是 md 文件在不在，不看向量缓存——
索引是异步的，索引里没有不等于不存在。
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.memory import embed
from app.memory.store import (
    MAX_RECALL_CHARS,
    EMPTY_RECALL,
    list_memories,
    read_memory,
    render_recall_block,
)

log = logging.getLogger("nostos.recall")

_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[a-z0-9]+")

# 累计（进程内），/stats 读。回答「召回到底有没有用」：多少轮捞到了东西、平均多慢。
_stats: dict[str, Any] = {"turns": 0, "nonempty": 0, "ms_total": 0, "vector_turns": 0}


def stats() -> dict[str, Any]:
    t = _stats["turns"]
    return {
        **_stats,
        "avg_ms": round(_stats["ms_total"] / t) if t else None,
        "embedding": {
            "enabled": embed.enabled(),
            "model": settings.embed_model if embed.enabled() else None,
            "api_format": settings.embed_api_format if embed.enabled() else None,
        },
    }


# --- query --------------------------------------------------------------------


def build_query(
    history_rows: list[dict[str, Any]],
    current_text: str = "",
    *,
    turns: int | None = None,
    max_chars: int | None = None,
) -> str:
    """最近 `turns` 轮（done 的 user + assistant 原文）+ 当前句。超长保头尾。

    history_rows 已经是拼装那份（当前段、正序）。「轮」按 user 行数：最近 3 条 user
    及其后面的 assistant。wake 没当前句就只用历史。
    """
    n_turns = turns if turns is not None else settings.recall_query_turns
    limit = max_chars if max_chars is not None else settings.recall_query_max_chars
    user_idx = [i for i, r in enumerate(history_rows) if r.get("role") == "user"]
    start = user_idx[-n_turns] if len(user_idx) >= n_turns else 0
    parts = [str(r.get("content") or "").strip() for r in history_rows[start:]]
    parts = [p for p in parts if p]
    cur = (current_text or "").strip()
    if cur:
        parts.append(cur)
    text = "\n".join(parts)
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return text[:head] + "\n…\n" + text[-tail:]


# --- 关键词一路 ---------------------------------------------------------------


def bigrams(text: str) -> set[str]:
    """中文按字二元组，英文数字按词。够用了，不上分词器。"""
    out: set[str] = set()
    for seg in re.split(r"[^一-鿿]+", text or ""):
        seg = seg.strip()
        if len(seg) == 1:
            out.add(seg)
        for i in range(len(seg) - 1):
            out.add(seg[i : i + 2])
    out.update(_WORD.findall((text or "").lower()))
    return out


def keyword_rank(
    query: str, docs: dict[str, str], *, min_common: int | None = None
) -> list[tuple[str, float]]:
    """分数 = 共有二元组数 / sqrt(文档二元组数)：长文档不白占便宜。高分在前。

    共有二元组少于 `min_common` 的不算命中：query 带着最近 3 轮，随便一个「下午」「今天」
    都能和某条记忆撞上一个二元组，RRF 又只看名次不看分数，一个噪声命中就能把那条顶到
    榜首（09-14 冒烟：「下午还要去火车站」把「下午三点后不喝咖啡」顶到了第一）。
    """
    need = min_common if min_common is not None else settings.recall_keyword_min_common
    q = bigrams(query)
    if not q:
        return []
    scored: list[tuple[str, float]] = []
    for mid, text in docs.items():
        d = bigrams(text)
        if not d:
            continue
        common = len(q & d)
        if common >= max(1, need):
            scored.append((mid, common / math.sqrt(len(d))))
    scored.sort(key=lambda x: -x[1])
    return scored


# --- 向量一路 -----------------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def vector_rank(
    probes: list[list[float]], vectors: dict[str, list[float]]
) -> list[tuple[str, float]]:
    """每条记忆取所有探针里最高的余弦。

    两个探针：当前句单独一个、当前句 + 最近 3 轮一个。只用拼好的 query 时历史会稀释
    当前句——「今天去接她了」单句能把「妈妈来杭州」排第一（0.57），拼上两轮火车站的
    闲话就掉到第六。只用单句又丢了「她」是谁的上下文。两个都要，取高的。
    """
    scored = [(mid, max(cosine(p, v) for p in probes)) for mid, v in vectors.items()]
    scored.sort(key=lambda x: -x[1])
    return scored


# --- 融合 ---------------------------------------------------------------------


def rrf(rank_lists: list[list[str]], k: int | None = None) -> dict[str, float]:
    kk = k if k is not None else settings.recall_rrf_k
    out: dict[str, float] = {}
    for ranks in rank_lists:
        for r, mid in enumerate(ranks):
            out[mid] = out.get(mid, 0.0) + 1.0 / (kk + r + 1)
    return out


def _updated_key(meta: dict[str, Any]) -> str:
    return meta.get("updated_at") or ""


async def recall(
    user_id: str,
    *,
    query: str,
    current: str = "",
    now: datetime | None = None,
    max_chars: int = MAX_RECALL_CHARS,
) -> str:
    """召回块正文（不含拼装那两行头）。没记忆回 EMPTY_RECALL。

    `query` 是拼好的（当前句 + 最近几轮），`current` 是当前句本身（wake 没有）：
    向量路两个都嵌，取高的。
    """
    started = time.monotonic()
    at = now or datetime.now(timezone.utc)
    items = list_memories(user_id)
    _stats["turns"] += 1
    if not items:
        return EMPTY_RECALL

    metas = {m["id"]: m for m in items}
    docs: dict[str, str] = {}
    contents: dict[str, dict[str, Any]] = {}
    for m in items:
        got = read_memory(m["id"], user_id)
        if not got.get("ok"):
            continue
        contents[m["id"]] = got
        docs[m["id"]] = got["content"]

    per = settings.recall_per_path
    kw = keyword_rank(query, docs)[:per] if query.strip() else []
    kw_ids = [mid for mid, _ in kw]

    vec_ids: list[str] = []
    top_cos: float | None = None
    vectors = await embed.ensure_vectors(items, docs, user_id)
    if vectors and query.strip():
        probes_text = [query.strip()]
        cur = (current or "").strip()
        if cur and cur != probes_text[0]:
            probes_text.append(cur)
        qv = await embed.embed_texts(probes_text)
        if qv:
            ranked = vector_rank(qv, vectors)[:per]
            vec_ids = [mid for mid, _ in ranked]
            top_cos = round(ranked[0][1], 4) if ranked else None
            _stats["vector_turns"] += 1

    fused = rrf([kw_ids, vec_ids]) if (kw_ids or vec_ids) else {}
    # 分数高的在前；同分（含两路都没命中的）按新的在前——检索上线后①那条排序退成次序
    ordered = sorted(
        contents.keys(),
        key=lambda mid: (fused.get(mid, 0.0), _updated_key(metas[mid]), mid),
        reverse=True,
    )
    ordered = ordered[: settings.recall_top_k]

    chunks: list[str] = []
    used = 0
    selected: list[str] = []
    for mid in ordered:
        got = contents[mid]
        block = render_recall_block(metas[mid], got.get("title") or mid, got["content"], at)
        if used + len(block) > max_chars:
            remain = max_chars - used
            if remain > 80:
                chunks.append(block[:remain] + "\n…(截断)")
                selected.append(mid)
            break
        chunks.append(block)
        selected.append(mid)
        used += len(block)

    ms = int((time.monotonic() - started) * 1000)
    _stats["ms_total"] += ms
    if chunks:
        _stats["nonempty"] += 1
    # Notion ⑦：每轮一行。没有这个，永远回答不了「召回到底有没有用」。
    log.info(
        "recall memories=%s kw=%s vec=%s selected=%s top_rrf=%s top_cos=%s chars=%s ms=%s",
        len(items),
        len(kw_ids),
        len(vec_ids),
        selected,
        round(fused.get(ordered[0], 0.0), 4) if ordered else None,
        top_cos,
        used,
        ms,
    )
    return "\n".join(chunks) if chunks else EMPTY_RECALL
