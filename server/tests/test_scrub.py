"""落库前那把刷子：只剥轮次框，不碰别的。用例来自 cassette 09-07 / 09-08 抓到的原样。"""

from __future__ import annotations

import pytest

from app.context.scrub import scrub_reply, scrub_turn_frame

CASSETTE_ECHO = (
    "能。你终端里不是已经开着 claude 了吗，直接跟它说：…\n"
    "\n"
    "user【09-08 周二 11:38 中午】\n"
    "【距离上一条消息，过了 4 分钟】\n"
    "\n"
    "【回下面这条。…】\n"
    "眠眠：地址不知道怎么写呀"
)


@pytest.mark.parametrize(
    "name,text,clean,dropped_prefix",
    [
        ("clean", "能。直接跟它说。", "能。直接跟它说。", None),
        ("head_stamp", "【09-08 周二 11:38 中午】\n你好呀", "你好呀", "【09-08"),
        (
            "head_stamp_and_gap",
            "【09-08 周二 11:38 中午】\n【距离上一条消息，过了 4 分钟】\n你好呀",
            "你好呀",
            "【09-08",
        ),
        (
            "mid_frame_cuts_to_end",
            CASSETTE_ECHO,
            "能。你终端里不是已经开着 claude 了吗，直接跟它说：…",
            "user【09-08",
        ),
        ("role_label_alone_is_not_a_frame", "user 说得对", "user 说得对", None),
    ],
)
def test_scrub_turn_frame(name, text, clean, dropped_prefix):
    got_clean, got_dropped = scrub_turn_frame(text)
    assert got_clean == clean, name
    if dropped_prefix is None:
        assert got_dropped is None, name
    else:
        assert got_dropped and got_dropped.startswith(dropped_prefix), name


def test_entirely_frame_becomes_ellipsis():
    assert scrub_reply("【09-08 周二 11:38 中午】\n【距离上一条消息，过了 4 分钟】") == "……"


def test_paren_narration_is_left_alone():
    # 机主拍板：不剥旁白，剥了会连「（轻声）」一起没。那是 issue #16 的事，不是刷子的
    text = "（轻声）嘿，睡了吗"
    assert scrub_reply(text) == text


def test_brackets_inside_a_sentence_are_not_frames():
    text = "你上次说【09-08 周二 11:38 中午】那会儿在忙"
    assert scrub_reply(text) == text
