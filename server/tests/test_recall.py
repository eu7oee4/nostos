"""召回：query 拼最近几轮、关键词 + 向量两路、RRF、预算、sha256 重嵌、退化（Notion ②⑤⑥⑦）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.memory import embed, recall as R
from app.memory.store import EMPTY_RECALL, delete_memory, list_memories, write_memory
from app.memory.store import _load_index, _save_index

UID = "local"
NOW = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)


def _rows(*pairs: tuple[str, str]) -> list[dict]:
    out = []
    for u, a in pairs:
        out.append({"role": "user", "content": u, "created_at": "2026-09-14T03:00:00Z"})
        out.append({"role": "assistant", "content": a, "created_at": "2026-09-14T03:00:01Z"})
    return out


def _set_updated(mem_id: str, iso: str) -> None:
    data = _load_index(UID)
    for m in data["memories"]:
        if m["id"] == mem_id:
            m["updated_at"] = iso
    _save_index(data, UID)


# --- query --------------------------------------------------------------------


def test_build_query_takes_last_three_turns_plus_current():
    rows = _rows(("一", "回一"), ("二", "回二"), ("三", "回三"), ("四", "回四"))
    assert R.build_query(rows, "今天去接她了", turns=3) == "二\n回二\n三\n回三\n四\n回四\n今天去接她了"
    assert R.build_query(rows[:2], "x", turns=3) == "一\n回一\nx"
    assert R.build_query([], "只有当前句") == "只有当前句"
    assert R.build_query(rows[:2]) == "一\n回一"  # wake：没当前句


def test_build_query_keeps_head_and_tail_when_too_long():
    rows = _rows(("头" * 100, "中" * 100))
    q = R.build_query(rows, "尾" * 100, max_chars=120)
    assert q.startswith("头" * 60) and q.endswith("尾" * 60) and "\n…\n" in q
    assert len(q) == 120 + 3


# --- 关键词 / 向量 / RRF --------------------------------------------------------


def test_bigrams_chinese_and_words():
    assert R.bigrams("妈妈来杭州 hello World 42") == {
        "妈妈", "妈来", "来杭", "杭州", "hello", "world", "42",
    }
    assert R.bigrams("她") == {"她"}


def test_keyword_rank_prefers_short_matching_docs():
    noise = "".join(chr(0x4E00 + i) for i in range(200))  # 200 个不重复的字 = 200 个二元组
    docs = {"mom": "妈妈 9 月中旬来杭州小住", "cat": "猫叫团子", "long": "妈妈来杭州" + noise}
    ranked = R.keyword_rank("我妈妈下周来杭州", docs, min_common=2)
    ids = [mid for mid, _ in ranked]
    assert ids[0] == "mom" and "cat" not in ids and ids[-1] == "long"
    assert R.keyword_rank("", docs) == []
    # 只撞上一个二元组（「下午」）不算命中
    docs = {"coffee": "下午三点后不喝咖啡", "mom": "妈妈来杭州"}
    assert R.keyword_rank("下午还要去火车站", docs, min_common=2) == []
    assert [m for m, _ in R.keyword_rank("下午还要去火车站", docs, min_common=1)] == ["coffee"]


def test_vector_rank_takes_best_probe():
    vectors = {"mom": [1.0, 0.0], "cat": [0.0, 1.0]}
    # 探针 1（拼了历史）偏猫，探针 2（当前句）偏妈：妈取探针 2 的分，两条都是 1.0，稳定排
    ranked = R.vector_rank([[0.1, 0.99], [1.0, 0.0]], vectors)
    assert {m: round(s, 2) for m, s in ranked} == {"mom": 1.0, "cat": 0.99}


def test_rrf_sums_over_paths():
    fused = R.rrf([["a", "b"], ["b", "c"]], k=60)
    assert fused["b"] == pytest.approx(1 / 61 + 1 / 62)
    assert fused["a"] == pytest.approx(1 / 61) and fused["c"] == pytest.approx(1 / 62)
    assert R.cosine([1, 0], [1, 0]) == 1.0 and R.cosine([1, 0], [0, 1]) == 0.0
    assert R.cosine([0, 0], [1, 1]) == 0.0


# --- recall：退化 / 排序 / 预算 --------------------------------------------------


@pytest.mark.anyio
async def test_recall_without_embedding_orders_hits_first_then_newest(data_dir, monkeypatch):
    monkeypatch.setattr(settings, "embed_base_url", "")
    write_memory("cat", "猫叫团子", user_id=UID)
    write_memory("mom-visit", "妈妈 9 月中旬来杭州小住", user_id=UID)
    write_memory("hometown", "住在上海", user_id=UID)
    _set_updated("cat", "2026-09-10T00:00:00Z")
    _set_updated("mom-visit", "2026-09-03T00:00:00Z")
    _set_updated("hometown", "2026-09-01T00:00:00Z")

    text = await R.recall(UID, query="妈妈下周来杭州", now=NOW)
    heads = [ln for ln in text.splitlines() if ln.startswith("### ")]
    # 命中的在前（虽然更旧），没命中的按新的在前；每条仍带日期
    assert [h.split("(`")[1].split("`)")[0] for h in heads] == ["mom-visit", "cat", "hometown"]
    assert "记于 09-03" in heads[0]
    assert R.stats()["embedding"]["enabled"] is False
    # 空 query（wake 且没历史）：不排名，纯新的在前
    text = await R.recall(UID, query="", now=NOW)
    heads = [ln for ln in text.splitlines() if ln.startswith("### ")]
    assert [h.split("(`")[1].split("`)")[0] for h in heads] == ["cat", "mom-visit", "hometown"]


@pytest.mark.anyio
async def test_recall_empty_and_top_k_and_budget(data_dir, monkeypatch):
    monkeypatch.setattr(settings, "embed_base_url", "")
    assert await R.recall(UID, query="x", now=NOW) == EMPTY_RECALL
    monkeypatch.setattr(settings, "recall_top_k", 2)
    for i in range(4):
        write_memory(f"m{i}", f"第{i}条", user_id=UID)
    text = await R.recall(UID, query="没有命中", now=NOW)
    assert text.count("### ") == 2
    monkeypatch.setattr(settings, "recall_top_k", 16)
    text = await R.recall(UID, query="没有命中", now=NOW, max_chars=60)
    assert text.count("### ") <= 2 and len(text) <= 70


# --- 向量缓存：sha256 / 签名 / 删除 / 服务挂了 ------------------------------------


def _fake_embedder(store: dict):
    """按文本给一个确定的小向量；记下调了几次、嵌了什么。"""
    async def _embed(texts):
        store["calls"].append(list(texts))
        out = []
        for t in texts:
            if "妈" in t:
                out.append([1.0, 0.0])
            elif "猫" in t:
                out.append([0.0, 1.0])
            else:
                out.append([0.7, 0.7])
        return out

    return _embed


@pytest.mark.anyio
async def test_vectors_cached_by_sha_and_signature(data_dir, monkeypatch):
    monkeypatch.setattr(settings, "embed_base_url", "http://fake")
    calls = {"calls": []}
    monkeypatch.setattr(embed, "embed_texts", _fake_embedder(calls))
    write_memory("mom", "妈妈来杭州", user_id=UID)
    write_memory("cat", "猫叫团子", user_id=UID)

    text = await R.recall(UID, query="今天去接她了，我妈", now=NOW)
    heads = [ln for ln in text.splitlines() if ln.startswith("### ")]
    assert "(`mom`)" in heads[0]
    # 第一次：两条记忆一批（嵌的是整个 md 正文，含 H1）+ query 一次
    assert calls["calls"][0] == ["# mom\n\n妈妈来杭州\n", "# cat\n\n猫叫团子\n"]
    assert len(calls["calls"]) == 2
    cache = json.loads((data_dir / "memories" / UID / "vectors.json").read_text())
    assert cache["signature"] == "ollama:bge-m3:2" and set(cache["vectors"]) == {"mom", "cat"}
    sha_before = cache["vectors"]["mom"]["sha256"]

    # 第二次：什么都没变，只嵌 query
    await R.recall(UID, query="随便", now=NOW)
    assert len(calls["calls"]) == 3 and calls["calls"][-1] == ["随便"]

    # 用户手改 md（杭州 → 上海）：sha 变了，只重嵌那一条
    (data_dir / "memories" / UID / "mom.md").write_text("# mom\n\n妈妈来上海\n", encoding="utf-8")
    await R.recall(UID, query="随便", now=NOW)
    assert calls["calls"][-2] == ["# mom\n\n妈妈来上海\n"]
    cache = json.loads((data_dir / "memories" / UID / "vectors.json").read_text())
    assert cache["vectors"]["mom"]["sha256"] != sha_before

    # 换模型：签名不同，整份重算
    monkeypatch.setattr(settings, "embed_model", "other")
    await R.recall(UID, query="随便", now=NOW)
    assert sorted(calls["calls"][-2]) == sorted(["# mom\n\n妈妈来上海\n", "# cat\n\n猫叫团子\n"])
    cache = json.loads((data_dir / "memories" / UID / "vectors.json").read_text())
    assert cache["signature"] == "ollama:other:2"

    # 删记忆：缓存跟着删
    delete_memory("cat", UID)
    cache = json.loads((data_dir / "memories" / UID / "vectors.json").read_text())
    assert set(cache["vectors"]) == {"mom"}
    assert [m["id"] for m in list_memories(UID)] == ["mom"]


@pytest.mark.anyio
async def test_embedding_service_down_falls_back_to_keywords(data_dir, monkeypatch):
    monkeypatch.setattr(settings, "embed_base_url", "http://fake")

    async def _down(texts):
        return None

    monkeypatch.setattr(embed, "embed_texts", _down)
    write_memory("mom", "妈妈来杭州", user_id=UID)
    write_memory("cat", "猫叫团子", user_id=UID)
    text = await R.recall(UID, query="我妈", now=NOW)
    heads = [ln for ln in text.splitlines() if ln.startswith("### ")]
    assert "(`mom`)" in heads[0] and len(heads) == 2
    assert not (data_dir / "memories" / UID / "vectors.json").exists()
    assert R.stats()["embedding"]["enabled"] is True


@pytest.mark.anyio
async def test_embed_texts_real_shape_via_httpx_mock(monkeypatch):
    """不打真服务：把 httpx 的 post 换掉，验两种 api_format 的请求路径和解析。"""
    import httpx

    seen = {}

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._p

    async def _post(self, url, json=None, headers=None):
        seen["url"] = url
        seen["json"] = json
        if url.endswith("/api/embed"):
            return _Resp({"embeddings": [[0.1, 0.2]] * len(json["input"])})
        return _Resp({"data": [{"index": i, "embedding": [0.3, 0.4]} for i in range(len(json["input"]))]})

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    monkeypatch.setattr(settings, "embed_base_url", "http://ollama:11434")
    monkeypatch.setattr(settings, "embed_api_format", "ollama")
    assert await embed.embed_texts(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
    assert seen["url"] == "http://ollama:11434/api/embed" and seen["json"]["model"] == "bge-m3"

    monkeypatch.setattr(settings, "embed_base_url", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(settings, "embed_api_format", "openai")
    monkeypatch.setattr(settings, "embed_model", "BAAI/bge-m3")
    assert await embed.embed_texts(["a"]) == [[0.3, 0.4]]
    assert seen["url"] == "https://api.siliconflow.cn/v1/embeddings"

    monkeypatch.setattr(settings, "embed_base_url", "")
    assert await embed.embed_texts(["a"]) is None
