"""回放探针：拿库里**真实的**对话前缀重放某几句，看模型肯不肯调 memory_write_item / _feel。

09-16 真机聊了 18 轮，事实一堆（在哪实习、想做什么方向、在投哪家），`tool_calls=0`。
同一套工具在 probe_memory_feel.py 里（只有 2 轮历史）对「我妈下周来杭州」5/5 开火。
这根探针回答：是长历史压住了工具调用，还是别的。每句两种前缀各跑 REPEATS 遍：

    full   当前段的全部历史（和线上那轮一模一样）
    short  只带那句之前的最近 2 轮

跑法（仓库根目录，读 .env 和 ./data 的真库，只读不写）：

    .venv/bin/python eval/probe_replay_tools.py 63 65 77

参数是要回放的 user 消息 id（`GET /messages` 里看）。
"""

from __future__ import annotations

import asyncio
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
os.environ["PROACTIVE_ENABLED"] = "false"

from app import db  # noqa: E402
from app.chat_loop import CHAT_TOOL_NAMES  # noqa: E402
from app.config import settings  # noqa: E402
from app.context.assemble import Trigger, build_messages  # noqa: E402
from app.llm import chat_completion  # noqa: E402
from app.memory.recall import build_query, recall  # noqa: E402
from app.nostools.registry import registry  # noqa: E402

REPEATS = 3
WRITE_TOOLS = ("memory_write_item", "memory_write_feel", "memory_write")


async def one(tools, messages) -> list[str]:
    try:
        msg = await chat_completion(messages, tools=tools, tool_choice="auto")
    except Exception as e:  # noqa: BLE001
        print(f"  ! {e}")
        return ["<error>"]
    return [c["function"]["name"] for c in (msg.get("tool_calls") or [])]


async def main(ids: list[int]) -> None:
    uid = settings.user_id
    tools = registry.openai_tools(CHAT_TOOL_NAMES)
    seg = await db.ensure_segment(uid)
    all_rows = await db.list_segment_turns(uid, int(seg["tail_from_msg_id"]))
    print(f"model = {settings.llm_model}  segment={seg['id']} rows={len(all_rows)}")
    for mid in ids:
        idx = next((i for i, r in enumerate(all_rows) if r["id"] == mid), None)
        if idx is None or all_rows[idx]["role"] != "user":
            print(f"\n[{mid}] not a user row in current segment, skip")
            continue
        text = all_rows[idx]["content"]
        before = all_rows[:idx]
        variants = {"full": before, "short": before[-4:]}
        print(f"\n[{mid}] {text!r}")
        for label, hist in variants.items():
            rc = await recall(uid, query=build_query(hist, text), current=text)
            msgs = build_messages(
                user_id=uid,
                history_rows=hist,
                recall=rc,
                trigger=Trigger(kind="user", text=text),
                episode=seg.get("episode_text") if label == "full" else None,
            )
            results = await asyncio.gather(*[one(tools, msgs) for _ in range(REPEATS)])
            hits = sum(1 for names in results if any(n in WRITE_TOOLS for n in names))
            called = sorted({n for names in results for n in names})
            print(f"  {label:6s} history={len(hist):3d} rows  write {hits}/{REPEATS}  called={called}")


if __name__ == "__main__":
    asyncio.run(main([int(a) for a in sys.argv[1:]] or [63, 65, 77]))
