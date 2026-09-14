"""记忆存储：item / feel 两种 kind、倒序召回带日期、用户可删（Notion「记忆系统设计对照」①⑩⑫）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.context.assemble import RECALL_BOUNDARY, RECALL_HEADER, Trigger, build_messages
from app.main import app
from app.memory import delete_memory, list_memories, read_memory, recall_text, write_memory
from app.memory.store import _load_index, _save_index

NOW = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)


def _set_updated(mem_id: str, iso: str) -> None:
    data = _load_index("local")
    for m in data["memories"]:
        if m["id"] == mem_id:
            m["updated_at"] = iso
    _save_index(data, "local")


def test_item_and_feel_carry_kind_and_intensity(data_dir):
    out = write_memory("mom-visit", "妈妈 9 月中旬来杭州小住", user_id="local", kind="item")
    assert out["ok"] and out["kind"] == "item" and out["intensity"] is None

    out = write_memory(
        "mom-tension", "和妈妈长时间相处会紧张", user_id="local", kind="feel", intensity="mid"
    )
    assert out["ok"] and out["kind"] == "feel" and out["intensity"] == "mid"

    got = read_memory("mom-tension", "local")
    assert got["kind"] == "feel" and got["intensity"] == "mid"
    index = json.loads((data_dir / "memories" / "local" / "index.json").read_text())
    by_id = {m["id"]: m for m in index["memories"]}
    assert by_id["mom-tension"]["intensity"] == "mid"
    assert by_id["mom-visit"]["kind"] == "item"


def test_feel_requires_valid_intensity(data_dir):
    assert write_memory("x", "y", user_id="local", kind="feel")["ok"] is False
    assert write_memory("x", "y", user_id="local", kind="feel", intensity="huge")["ok"] is False
    assert write_memory("x", "y", user_id="local", kind="nope")["ok"] is False
    assert not (data_dir / "memories" / "local" / "x.md").exists()


def test_legacy_files_default_to_item(data_dir):
    root = data_dir / "memories" / "local"
    root.mkdir(parents=True)
    (root / "hometown.md").write_text("# 家乡\n\n上海\n", encoding="utf-8")
    # 老 index：没有 kind / intensity
    (root / "index.json").write_text(
        json.dumps({"memories": [{"id": "hometown", "title": "家乡", "updated_at": "2026-09-06T16:21:52Z"}]}),
        encoding="utf-8",
    )
    items = list_memories("local")
    assert items == [
        {
            "id": "hometown",
            "title": "家乡",
            "kind": "item",
            "intensity": None,
            "updated_at": "2026-09-06T16:21:52Z",
            "path": "memories/local/hometown.md",
        }
    ]


def test_list_is_newest_first_not_alphabetical(data_dir):
    for mem_id in ("zeta", "alpha", "mid"):
        write_memory(mem_id, mem_id, user_id="local")
    _set_updated("zeta", "2026-09-01T00:00:00Z")
    _set_updated("alpha", "2026-09-03T00:00:00Z")
    _set_updated("mid", "2026-09-10T00:00:00Z")
    assert [m["id"] for m in list_memories("local")] == ["mid", "alpha", "zeta"]


def test_recall_blocks_carry_date_and_are_newest_first(data_dir):
    write_memory("mom-visit", "妈妈 9 月中旬来杭州小住", user_id="local")
    write_memory("mom-tension", "和妈妈长时间相处会紧张", user_id="local", kind="feel", intensity="mid")
    write_memory("old-job", "去年在上海做设计", user_id="local")
    _set_updated("mom-visit", "2026-09-03T02:00:00Z")
    _set_updated("mom-tension", "2026-09-03T02:00:01Z")
    _set_updated("old-job", "2025-11-20T02:00:00Z")

    text = recall_text("local", now=NOW)
    lines = [ln for ln in text.splitlines() if ln.startswith("### ")]
    assert lines == [
        "### mom-tension (`mom-tension`) | 感受，浓度中 | 记于 09-03",
        "### mom-visit (`mom-visit`) | 记于 09-03",
        # 跨年才带年份
        "### old-job (`old-job`) | 记于 2025-11-20",
    ]


def test_recall_truncates_oldest_first(data_dir):
    write_memory("new", "新" * 100, user_id="local")
    write_memory("old", "旧" * 100, user_id="local")
    _set_updated("new", "2026-09-10T00:00:00Z")
    _set_updated("old", "2026-09-01T00:00:00Z")
    # 第一块约 136 字符整块进；剩下 100 多给旧的那块，砍到预算处加截断标
    text = recall_text("local", max_chars=250, now=NOW)
    assert "新" * 100 in text
    assert "旧旧" in text and "旧" * 100 not in text and text.endswith("…(截断)")
    assert text.index("(`new`)") < text.index("(`old`)")


def test_recall_block_starts_with_header_and_boundary(data_dir):
    msgs = build_messages(
        user_id="local",
        history_rows=[],
        recall="### 家乡 (`hometown`) | 记于 09-03\n上海\n",
        trigger=Trigger(kind="user", text="我住哪"),
        now=NOW,
    )
    block = msgs[-2]
    assert block["role"] == "system"
    assert block["content"].split("\n")[:2] == [RECALL_HEADER, RECALL_BOUNDARY]
    assert block["content"].endswith("记于 09-03\n上海")


def test_delete_removes_file_and_index_entry(data_dir):
    write_memory("gone", "要删的", user_id="local")
    write_memory("kept", "留着的", user_id="local")
    assert delete_memory("gone", "local") is True
    assert delete_memory("gone", "local") is False
    assert not (data_dir / "memories" / "local" / "gone.md").exists()
    assert [m["id"] for m in list_memories("local")] == ["kept"]
    assert [m["id"] for m in _load_index("local")["memories"]] == ["kept"]


@pytest.fixture
def client(data_dir):
    with TestClient(app) as c:
        yield c


def test_delete_route_is_the_user_exit(client):
    write_memory("secret", "不想留的", user_id="local")
    assert client.get("/memories").json()["memories"][0]["kind"] == "item"
    r = client.delete("/memories/secret")
    assert r.status_code == 200 and r.json() == {"ok": True, "id": "secret"}
    assert client.delete("/memories/secret").status_code == 404
    assert client.get("/memories/secret").status_code == 404
    assert client.get("/memories").json()["memories"] == []
