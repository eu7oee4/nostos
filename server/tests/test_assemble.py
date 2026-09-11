"""拼装单的形状（PROMPT_ASSEMBLY.md 四条纪律）。

这些断言是那份文档的可执行版：谁把 assistant 行加上戳、把「距离」冻进历史、
把召回挪回前缀，这里先红。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.context.assemble import Trigger, build_messages, format_gap, format_stamp

NOW = datetime(2026, 9, 8, 3, 38, tzinfo=timezone.utc)  # 11:38 周二 上海
HISTORY = [
    {"role": "user", "content": "我是谁", "created_at": "2026-09-08T03:24:00Z"},
    {"role": "assistant", "content": "你是眠眠。", "created_at": "2026-09-08T03:24:05Z"},
]


def test_stamp_format_is_the_only_one():
    assert format_stamp("2026-09-08T03:24:00Z", "Asia/Shanghai") == "09-08 周二 11:24 中午"
    assert format_stamp("2026-09-12T15:30:00Z", "Asia/Shanghai") == "09-12 周六 23:30 深夜"


def test_gap_wording():
    assert format_gap(30) == "不到 1 分钟"
    assert format_gap(13 * 60 + 55) == "13 分钟"
    assert format_gap(8 * 3600) == "8 小时"
    assert format_gap(3 * 86400) == "3 天"


def test_user_rows_stamped_assistant_rows_bare(data_dir):
    msgs = build_messages(
        user_id="local",
        history_rows=HISTORY,
        recall="",
        trigger=Trigger(kind="user", text="地址不知道怎么写呀"),
        now=NOW,
    )
    assert msgs[0]["role"] == "system"

    users = [m for m in msgs if m["role"] == "user"]
    assistants = [m for m in msgs if m["role"] == "assistant"]

    # 历史 user 轮：只有戳，没有「距离」
    assert users[0]["content"] == "【09-08 周二 11:24 中午】\n我是谁"
    # assistant 行不打戳、不加框
    assert assistants == [{"role": "assistant", "content": "你是眠眠。"}]
    # 当前轮和历史轮同构，只多一行「距离」
    assert msgs[-1] == {
        "role": "user",
        "content": "【09-08 周二 11:38 中午】\n【距离上一条消息，过了 13 分钟】\n地址不知道怎么写呀",
    }
    # 不写「称呼：」前缀
    assert not any("：" in m["content"].split("\n")[-1][:4] for m in users)


def test_recall_sits_after_history_before_trigger(data_dir):
    msgs = build_messages(
        user_id="local",
        history_rows=HISTORY,
        recall="### 家乡 (`hometown`)\n上海\n",
        trigger=Trigger(kind="user", text="我住哪"),
        now=NOW,
    )
    idx = [
        i
        for i, m in enumerate(msgs)
        if m["role"] == "system" and m["content"].startswith("现在浮现在你脑海里的记忆有：")
    ]
    assert idx == [len(msgs) - 2]
    # 历史都在召回前面
    assert all(m["role"] != "system" for m in msgs[idx[0] - len(HISTORY) : idx[0]])


def test_empty_recall_leaves_no_placeholder(data_dir):
    for recall in ("", "（暂无长期记忆）", "   "):
        msgs = build_messages(
            user_id="local",
            history_rows=HISTORY,
            recall=recall,
            trigger=Trigger(kind="user", text="hi"),
            now=NOW,
        )
        assert not any("浮现" in m["content"] for m in msgs if m["role"] == "system")


def test_wake_trigger_uses_corner_brackets(data_dir):
    msgs = build_messages(
        user_id="local",
        history_rows=HISTORY,
        recall="",
        trigger=Trigger(kind="wake", intent="check_in"),
        now=NOW,
    )
    last = msgs[-1]
    assert last["role"] == "user"
    lines = last["content"].split("\n")
    assert lines[0] == "【09-08 周二 11:38 中午】"
    assert lines[1].startswith("【距离上一条消息")
    assert lines[2] == "〔轮到你说话。没有新消息。 你留的意图：check_in〕"


def test_no_history_means_no_gap_line(data_dir):
    msgs = build_messages(
        user_id="local",
        history_rows=[],
        recall="",
        trigger=Trigger(kind="user", text="第一句"),
        now=NOW,
    )
    assert msgs[-1]["content"] == "【09-08 周二 11:38 中午】\n第一句"


def test_prefix_order_system_profile_persona_prefs(data_dir):
    (data_dir / "user_profile.json").write_text(
        json.dumps({"nickname": "眠眠", "gender": "她"}), encoding="utf-8"
    )
    (data_dir / "persona.md").write_text("温柔、简洁。", encoding="utf-8")
    (data_dir / "prefs.json").write_text(
        json.dumps({"style": [{"id": "brevity", "text": "说短点"}], "wake": {}}),
        encoding="utf-8",
    )
    msgs = build_messages(
        user_id="local",
        history_rows=HISTORY,
        recall="",
        trigger=Trigger(kind="user", text="hi"),
        now=NOW,
    )
    systems = [m["content"] for m in msgs[:4]]
    assert all(m["role"] == "system" for m in msgs[:4])
    assert "眠眠" in systems[1]
    assert systems[2].startswith("【伙伴人格 / persona】")
    assert systems[3].startswith("【纠正过的说话方式】") and "说短点" in systems[3]
    # 第 5 条开始是对话
    assert msgs[4]["role"] == "user"
