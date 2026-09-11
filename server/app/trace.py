"""Turn id：把一轮里所有日志串起来。

一轮 = 一次 `run_chat` 或一次 `fire_wake`。这一轮里拼装、每次调模型、每个工具、
落库、推送打出来的日志，全带同一个 8 位 id，`grep <id>` 就是这一轮的完整链路。
没有它，`nostos.llm` 的 usage 行和 `nostos.chat` 的工具行是两条平行的流水，
「哪次调用花了多少」对不上号。

实现是一个 ContextVar：asyncio 任务之间各自独立，wake 和 chat 同时跑也不会串。
"""

from __future__ import annotations

import contextvars
import logging
import uuid

_turn: contextvars.ContextVar[str | None] = contextvars.ContextVar("nostos_turn", default=None)


def new_turn(prefix: str) -> str:
    """开一轮。prefix 是 `chat` / `wake`，看日志时一眼分得开来源。"""
    tid = f"{prefix}-{uuid.uuid4().hex[:8]}"
    _turn.set(tid)
    return tid


def current() -> str | None:
    return _turn.get()


class TurnFilter(logging.Filter):
    """给每条 record 挂 `turn` 字段，格式串里用 `%(turn)s`。轮外的日志是 `-`。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.turn = _turn.get() or "-"
        return True
