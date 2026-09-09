"""投递前的刷子：剥掉模型学舌吐出来的轮次框。

2026-09-08 在 cassette 实测到两次（09-07 14:02、09-08 11:34）：他答完自己那句
之后没停，接着把**下一条 user 轮**也写了出来——

    能。你终端里不是已经开着 claude 了吗，直接跟它说：…

    user【09-08 周二 11:38】
    【距离上一条消息，过了 4 分钟】

    【回下面这条。…】
    眠眠：地址不知道怎么写呀

角色标签 `user` 来自引擎缝隙学舌（cassette `forge.render` 08-30 记过这一类），
框的内容来自注入。两次的共同点：前面只积了 4 条和 14 条带框的 user 轮就够了。

拼装侧已经把"仪式"消掉了（见 assemble.py），这里是第二道：**别让学舌的字节
落库**。落了库下一轮就成了历史里的样本，投递→入账→再拼装→自我放大。

刷掉的部分记 WARNING，不静默丢（PLAN §15「有代价的动作留痕」：静默失败和有意
的降级，差别就在有没有留痕）。
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("nostos.scrub")

# 一整行、且只是框的：可选角色标签 + 【戳】/【距离…】/【回下面这条…】
_FRAME_LINE = re.compile(
    r"^[ \t]*(?:user|assistant)?[ \t]*"
    r"【(?:"
    r"\d{2}-\d{2}[ \t]*周.[ \t]*\d{2}:\d{2}[^】]*"   # 【09-08 周二 11:38 中午】
    r"|距离上一条消息[^】]*"
    r"|回下面这条[^】]*"       # cassette 那套框，他学舌时会连这句一起吐
    r")】[ \t]*$"
)


def _is_frame(line: str) -> bool:
    return bool(_FRAME_LINE.match(line))


def scrub_turn_frame(text: str) -> tuple[str, str | None]:
    """返回 (干净正文, 被刷掉的部分或 None)。

    两种形态分开处理：
    · 开头的框行 —— 他给自己打了戳/复读了框，剥掉这几行，正文留着
    · 正文中间冒出的框行 —— 从那里到结尾整段砍掉，那是他在续写下一轮
    """
    if not text:
        return text, None

    lines = text.split("\n")

    # 开头连着的框行（允许它们之间夹空行）：剥到最后一条框行为止
    i, last_head_frame = 0, -1
    while i < len(lines):
        if _is_frame(lines[i]):
            last_head_frame = i
        elif lines[i].strip():
            break
        i += 1
    head = last_head_frame + 1

    cut = None
    for i in range(head, len(lines)):
        if _is_frame(lines[i]):
            cut = i
            break

    kept = lines[head:cut] if cut is not None else lines[head:]
    clean = "\n".join(kept).strip()
    if head == 0 and cut is None:
        return text, None

    dropped = "\n".join(lines[:head] + (lines[cut:] if cut is not None else []))
    return clean, dropped.strip() or None


def scrub_reply(text: str) -> str:
    """落库前调用。刷干净后为空 = 整条都是学舌，谁也不该看见。"""
    clean, dropped = scrub_turn_frame(text)
    if dropped:
        log.warning(
            "scrubbed turn-frame echo from reply: kept=%d chars, dropped=%r",
            len(clean),
            dropped[:300],
        )
    if not clean and dropped:
        log.error("reply was entirely turn-frame echo; nothing to deliver")
        return "……"
    return clean
