"""感受探针：模型认不认得出「这句里有一份该记下来的感受」，肯不肯调 `memory_write_feel`。

Notion「记忆系统设计对照」第 ⑫ 点要求上线前先量：feel 和 prefs_write 是同一种难题，
都要在对话中认出「这句属于某个元类别」再停下来调工具。prefs_write 在 deepseek-chat 上
实测只有三到六成（issue #9，`eval/README.md`）。**能到八成再上，到不了就知道兜底逃不掉**
（形状见第 ⑨ 点：提炼时把本段已写的条目列给模型，只补漏掉的）。

量的不是回复好不好，是**它肯不肯调 `memory_write_feel`**；顺手也记 `memory_write_item`
有没有开火，看两路分不分得开。和 `probe_prefs_write.py` 同一套：一个场景、三种说法、
每格五遍、`tool_choice="auto"`、工具给聊天路径那全套。

跑法（在仓库根目录）：

    cd server && PYTHONPATH=. DATA_DIR=/tmp/probe ../.venv/bin/python \\
        ../eval/probe_memory_feel.py

换模型就改 `.env` 的 `LLM_MODEL` / `LLM_BASE_URL` 再跑一遍。
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))

for _line in (pathlib.Path(__file__).resolve().parents[1] / ".env").read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from app.chat_loop import CHAT_TOOL_NAMES  # noqa: E402
from app.config import settings  # noqa: E402
from app.context.system_prompt import get_system_prompt  # noqa: E402
from app.llm import chat_completion  # noqa: E402
from app.nostools.registry import registry  # noqa: E402

# 三种说法：事件 + 感受混在一句里（设计页的例子）、只有感受、感受藏在抱怨里
PHRASES = [
    "我妈下周来杭州住几天，有点紧张，我俩很久没长时间相处了",
    "其实我挺怕一个人过年的",
    "一想到明天要跟老板一对一就烦，每次都被他盯着问进度",
]

REPEATS = 5


async def one(tools: list[dict], phrase: str) -> dict[str, bool] | None:
    messages = [
        {"role": "system", "content": get_system_prompt()},
        {"role": "user", "content": "【09-14 周一 21:10 晚上】\n在吗"},
        {"role": "assistant", "content": "在。今天怎么样？"},
        {
            "role": "user",
            "content": (
                "【09-14 周一 21:12 晚上】\n"
                "【距离上一条消息，过了 2 分钟】\n" + phrase
            ),
        },
    ]
    try:
        msg = await chat_completion(messages, tools=tools, tool_choice="auto")
    except Exception as e:  # noqa: BLE001 — 探针，失败也要记下来
        print(f"  ! {e}")
        return None
    names = [c["function"]["name"] for c in (msg.get("tool_calls") or [])]
    return {
        "feel": "memory_write_feel" in names,
        "item": "memory_write_item" in names,
        "legacy": "memory_write" in names,
    }


async def main() -> None:
    tools = registry.openai_tools(CHAT_TOOL_NAMES)
    print(f"model = {settings.llm_model}  ({settings.llm_base_url})")
    print(f"tools = {[t['function']['name'] for t in tools]}")
    feel_hits = item_hits = runs = 0
    for phrase in PHRASES:
        results = [r for r in await asyncio.gather(*[one(tools, phrase) for _ in range(REPEATS)]) if r]
        f = sum(r["feel"] for r in results)
        i = sum(r["item"] for r in results)
        legacy = sum(r["legacy"] for r in results)
        feel_hits += f
        item_hits += i
        runs += len(results)
        extra = f"  (老名字 memory_write: {legacy})" if legacy else ""
        print(f"  {phrase[:22]:24s} feel {f}/{len(results)}   item {i}/{len(results)}{extra}")
    if runs:
        print(f"  合计 feel {feel_hits}/{runs} = {feel_hits / runs:.0%}   item {item_hits}/{runs} = {item_hits / runs:.0%}")


if __name__ == "__main__":
    asyncio.run(main())
