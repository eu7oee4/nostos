"""纠偏探针（PLAN §10.2）第一件：模型认不认得出「这句是在挑我说话方式」。

量的不是回复好不好，是**它肯不肯调 `prefs_write`**。不调 = 纠偏这轮之后就飘走了，
而「说一句就变了」是 PLAN §5.2③ 拍板的最强留存钩子。所以这条是选型的硬指标之一，
不是锦上添花。

⚠️ 这是单点探针，不是 §10.2 那套完整 eval（persona 库 × 用户模拟器 × 模型矩阵）。
它只跑一个场景的多次重复，回答「这个底座在这件事上稳不稳」。

跑法（在仓库根目录）：

    cd server && PYTHONPATH=. DATA_DIR=/tmp/probe ../.venv/bin/python \\
        ../eval/probe_prefs_write.py

换模型就改 `.env` 的 `LLM_MODEL` / `LLM_BASE_URL` 再跑一遍。
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

# 让脚本在 server/ 下以 PYTHONPATH=. 跑得起来
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))

for _line in (pathlib.Path(__file__).resolve().parents[1] / ".env").read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from app.config import settings  # noqa: E402
from app.context.system_prompt import get_system_prompt  # noqa: E402
from app.llm import chat_completion  # noqa: E402
from app.nostools.registry import registry  # noqa: E402

# 和聊天路径给的那一套一致（工具数量本身会稀释注意力，别只给一个）
TOOL_NAMES = [
    "memory_list",
    "memory_read",
    "memory_write",
    "prefs_write",
    "prefs_list",
    "wake_set",
    "wake_list",
    "wake_cancel",
]

# 三种说法，从最像指令到最像抱怨
PHRASES = [
    "你别这么客气，说短点",
    "别老反问我",
    "你说话能不能自然点，别老一套一套的",
]

REPEATS = 5


async def one(tools: list[dict], phrase: str) -> bool | None:
    """跑一轮，返回它有没有调 prefs_write。"""
    messages = [
        {"role": "system", "content": get_system_prompt()},
        {"role": "user", "content": "【09-09 周三 19:20 晚上】\n在吗，我今天有点累"},
        {"role": "assistant", "content": "在。怎么了？"},
        {
            "role": "user",
            "content": (
                "【09-09 周三 19:22 晚上】\n"
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
    return "prefs_write" in names


async def main() -> None:
    tools = registry.openai_tools(TOOL_NAMES)
    print(f"model = {settings.llm_model}  ({settings.llm_base_url})")
    total_hits = 0
    total_runs = 0
    for phrase in PHRASES:
        results = await asyncio.gather(*[one(tools, phrase) for _ in range(REPEATS)])
        hits = sum(1 for r in results if r is True)
        total_hits += hits
        total_runs += sum(1 for r in results if r is not None)
        print(f"  {phrase:24s} {hits}/{REPEATS}")
    rate = total_hits / total_runs if total_runs else 0.0
    print(f"  合计 {total_hits}/{total_runs} = {rate:.0%}")


if __name__ == "__main__":
    asyncio.run(main())
