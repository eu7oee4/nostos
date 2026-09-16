"""事件探针：模型肯不肯对一句「该记的事实」调 `memory_write_item`。

09-16 真机聊了 18 轮（实习在哪、想做什么方向、在投哪家），`tool_calls=0`。回放发现不是历史
长度的事（只带 2 轮也 0/3），是**话题**：描述例句里有「我换工作了」就 4/5，「我在…实习」
一个例子都没有就 0/5。和 #9 同一类：认不出「这句属于该记的」，杠杆只有描述文本。

两组句子：

    in_set    描述例句覆盖到的（数字会虚高，看的是「例句有没有起作用」）
    held_out  描述里没有的（看的是「有没有泛化」——09-16 的答案是没有）

每句 REPEATS 遍，脚手架和 probe_memory_feel.py 一样（2 轮历史、全套 9 个工具）。
描述文本用 registry 里**当前那份**，改了描述跑一遍就是对照。

跑法（仓库根目录）：

    .venv/bin/python eval/probe_item_write.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
for _line in (ROOT / ".env").read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())
os.environ["PROACTIVE_ENABLED"] = "false"

from app.chat_loop import CHAT_TOOL_NAMES  # noqa: E402
from app.config import settings  # noqa: E402
from app.context.system_prompt import get_system_prompt  # noqa: E402
from app.llm import chat_completion  # noqa: E402
from app.nostools.registry import registry  # noqa: E402

REPEATS = 5
A0 = "在。今天怎么样？"
A_ASK_WHERE = "一周多还没派活，正常。在杭州哪块儿实习？"
A_ASK_TIME = "你们几点下班？"

# (标签, 他上一句, 她这句)
IN_SET = [
    ("实习完整句", A0, "我在滨江一个AI短剧公司实习，刚来一周多，没干什么活"),
    ("方向", A0, "我想往AI产品经理方向做"),
    ("问答碎句", A_ASK_WHERE, "滨江 一个AI短剧公司"),
    ("在投宠物平台", A0, "想去个更靠产品的岗，在投一家正在做0→1的宠物平台"),
    ("眠眠", A0, "我叫眠眠，住在上海，喜欢深夜写代码"),
    ("妈妈", A0, "我妈下周来杭州住几天，有点紧张，我俩很久没长时间相处了"),
]
HELD_OUT = [
    ("出差", A0, "下周三要去上海出差两天"),
    ("学日语", A0, "最近在学日语，报了个班，一周两次"),
    ("姐姐生娃", A0, "我姐上周生了个女儿"),
    ("换leader", A0, "我们组换了个新leader，之前那个调去别的部门了"),
    ("问答·几点下班", A_ASK_TIME, "六点半 但一般都拖到七点"),
    ("闲聊·不该记", A0, "今天午饭吃的麻辣烫 有点咸"),
]


def _messages(prev_assistant: str, text: str) -> list[dict]:
    return [
        {"role": "system", "content": get_system_prompt()},
        {"role": "user", "content": "【09-16 周三 21:10 晚上】\n在吗"},
        {"role": "assistant", "content": prev_assistant},
        {
            "role": "user",
            "content": "【09-16 周三 21:12 晚上】\n【距离上一条消息，过了 2 分钟】\n" + text,
        },
    ]


async def one(tools: list[dict], msgs: list[dict]) -> list[str]:
    try:
        msg = await chat_completion(msgs, tools=tools, tool_choice="auto")
    except Exception as e:  # noqa: BLE001
        print(f"  ! {e}")
        return []
    return [c["function"]["name"] for c in (msg.get("tool_calls") or [])]


async def run(label: str, cases: list[tuple[str, str, str]], tools: list[dict]) -> None:
    print(f"\n== {label}")
    total = 0
    for name, prev, text in cases:
        results = await asyncio.gather(*[one(tools, _messages(prev, text)) for _ in range(REPEATS)])
        items = sum(1 for r in results if "memory_write_item" in r)
        feels = sum(1 for r in results if "memory_write_feel" in r)
        total += items
        print(f"  {name:10s} item {items}/{REPEATS}  feel {feels}/{REPEATS}")
    print(f"  合计 item {total}/{REPEATS * len(cases)}")


async def main() -> None:
    tools = registry.openai_tools(CHAT_TOOL_NAMES)
    print(f"model = {settings.llm_model}  ({settings.llm_base_url})")
    await run("in_set（描述例句覆盖到的）", IN_SET, tools)
    await run("held_out（描述里没有的）", HELD_OUT, tools)


if __name__ == "__main__":
    asyncio.run(main())
