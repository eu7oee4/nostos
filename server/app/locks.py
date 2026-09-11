"""按 user_id 的轮锁：同一个用户，一次只跑一轮。

要挡的两种交错（单用户也会发生）：

1. 用户连发两条 → 两个 `run_chat` 并行，各自读到的历史都缺对方那条，两句回复
   互相不知道对方存在，落库顺序还可能颠倒
2. 聊天正在工具循环里，wake 到点 → `fire_wake` 把一条 assistant 插进对话中间，
   而正在跑的那轮对此一无所知，回复落库时紧跟在 wake 那句后面，读起来像他自言自语

锁住整轮（含调模型的几秒到几十秒）：wake 等这轮说完再开口，第二条消息等第一条
回完再进拼装。这是 PLAN §15「任何当前用户不许是可变全局」在并发维度上的同一条。

⚠️ 持锁期间**不能**再去拿同一把锁（asyncio.Lock 不可重入）。`schedule_wake` 到点
即开火那条分支因此改成「挂进调度器立即执行」而不是 inline 调 `fire_wake`——
模型在聊天里 `wake_set(delay_seconds=0)` 时，聊天这轮正持着锁。
"""

from __future__ import annotations

import asyncio

_locks: dict[str, asyncio.Lock] = {}


def turn_lock(user_id: str) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock
