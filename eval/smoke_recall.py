"""召回冒烟：真向量服务（.env 的 EMBED_BASE_URL）+ 设计页那个例子。

09-03 记了「妈妈 9 月中旬来杭州小住」和「和妈妈长时间相处会紧张」，另有几条无关的；
09-10 说「今天去接她了」——单句没有一个字撞得上「妈妈」，关键词一路捞不到，看向量一路
（加最近 3 轮里聊到妈妈）能不能把那两条排到前面。

跑法（仓库根目录，需要 .env 里 EMBED_BASE_URL 指向能通的 ollama）：

    .venv/bin/python eval/smoke_recall.py

数据落 eval/results/smoke_recall/（已 gitignore）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
os.environ["DATA_DIR"] = str(ROOT / "eval" / "results" / "smoke_recall")
logging.basicConfig(level="INFO", format="%(name)s %(message)s")
logging.getLogger("httpx").setLevel("WARNING")

from app.config import settings  # noqa: E402
from app.memory import recall as R  # noqa: E402
from app.memory.store import _load_index, _save_index, write_memory  # noqa: E402

MEMS = [
    ("mom-visit", "妈妈 9 月中旬来杭州小住五天", "item", None, "2026-09-03T02:00:00Z"),
    ("mom-tension", "和妈妈长时间相处会紧张，怕自己一忙就烦，她看出来会难过", "feel", "mid", "2026-09-03T02:00:01Z"),
    ("hometown", "住在上海，老家在绍兴", "item", None, "2026-09-01T00:00:00Z"),
    ("cat", "猫叫团子，三岁，怕吹风机", "item", None, "2026-09-05T00:00:00Z"),
    ("job", "在做设计，最近在赶一个展厅项目，老板盯进度", "item", None, "2026-09-08T00:00:00Z"),
    ("boss-dread", "一想到跟老板一对一就烦", "feel", "mid", "2026-09-08T00:00:01Z"),
    ("coffee", "喝咖啡只喝美式，下午三点后不喝", "item", None, "2026-09-11T00:00:00Z"),
    ("run", "周末早上去西湖边跑步", "item", None, "2026-09-12T00:00:00Z"),
]

HISTORY = [
    {"role": "user", "content": "在吗，今天有点累", "created_at": "2026-09-10T10:00:00Z"},
    {"role": "assistant", "content": "在。怎么了？", "created_at": "2026-09-10T10:00:05Z"},
    {"role": "user", "content": "下午还要去火车站", "created_at": "2026-09-10T10:01:00Z"},
    {"role": "assistant", "content": "接人？", "created_at": "2026-09-10T10:01:05Z"},
]


async def main() -> None:
    print(f"embed = {settings.embed_api_format} {settings.embed_model} @ {settings.embed_base_url or '(off)'}")
    for mid, content, kind, intensity, at in MEMS:
        write_memory(mid, content, user_id="local", kind=kind, intensity=intensity)
    data = _load_index("local")
    stamps = {m[0]: m[4] for m in MEMS}
    for m in data["memories"]:
        m["updated_at"] = stamps[m["id"]]
    _save_index(data, "local")

    for label, history, cur in [
        ("单句，无历史", [], "今天去接她了"),
        ("单句 + 最近 2 轮（火车站 / 接人）", HISTORY, "今天去接她了"),
        ("关键词能撞上的", [], "我妈到杭州了"),
        ("无关的", [], "帮我想想晚饭吃什么"),
    ]:
        q = R.build_query(history, cur)
        text = await R.recall("local", query=q, current=cur)
        heads = [ln.split("(`")[1].split("`)")[0] for ln in text.splitlines() if ln.startswith("### ")]
        print(f"\n[{label}]  query={q!r}")
        print("  ", " > ".join(heads[:5]), "…" if len(heads) > 5 else "")
    print("\nstats", R.stats())


asyncio.run(main())
