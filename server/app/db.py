"""SQLite: messages + wakes + push subscriptions. Server stamps created_at."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from app.config import settings

log = logging.getLogger("nostos.db")

DB_NAME = "nostos.sqlite"

# messages 的列清单也只写一份
MSG_COLS = "id, user_id, role, content, status, reason, created_at"

# wakes 的列清单只写一份：加一列时不用四处找 SELECT
WAKE_COLS = "id, user_id, wake_at, note, intent, status, source, reason, created_at, fired_at"

# wakes 表本体单独拎出来：迁移（重建表）和首建共用同一份定义，两处不会漂。
#
# status 四态：
#   pending    等着开火
#   fired      开过火了，fired_at 是真实开火时刻
#   cancelled  **人**取消的：用户 / 模型 wake_cancel、用户把随机关掉
#   skipped    **系统**没让它开：护栏挡了（quiet_hours / daily_cap / …）、停机期间
#              过期（missed）、已武装那条不再合规被重挑（repick）
# cancelled 和 skipped 分开，是 Raven 那条「失败降级要留痕」：静默失败和有意的
# 降级，差别就在有没有留痕。以前两种都记成 cancelled，事后分不清是人不要还是
# 系统没给。reason 是短 slug，只在 cancelled / skipped 时有值。
WAKES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    wake_at TEXT NOT NULL,
    note TEXT,
    intent TEXT NOT NULL DEFAULT 'check_in',
    status TEXT NOT NULL CHECK (status IN ('pending', 'fired', 'cancelled', 'skipped')),
    -- manual = 模型 wake_set / POST /wakes 定的；auto = 随机醒来挑的（#10）。
    -- 护栏只数 auto 那些，manual 不受日上限/最小间隔约束。
    source TEXT NOT NULL DEFAULT 'manual',
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    fired_at TEXT
);
"""

# messages 的 status 只对 user 行有意义（一轮的状态挂在触发它的那句上）；
# assistant / wake 写进来的行直接 done。
#   pending  用户句落了，回复还没来
#   done     回复已落库——和 INSERT 回复是**同一个事务**（complete_turn_tx）
#   failed   模型挂了 / 进程重启。reason 短 slug：llm_502 / llm_401 / error / restart
# 模型只看 done 的轮次（list_recent_turns）；前端三种都看，failed 的能重发，
# 重发是把同一行 failed → pending 再跑，不重插。
SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done' CHECK (status IN ('pending', 'done', 'failed')),
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_user_created
    ON messages (user_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_messages_user_status
    ON messages (user_id, status);
""" + WAKES_TABLE_SQL + """
CREATE INDEX IF NOT EXISTS idx_wakes_user_status_at
    ON wakes (user_id, status, wake_at);
CREATE INDEX IF NOT EXISTS idx_wakes_user_source_fired
    ON wakes (user_id, source, status, fired_at);

-- Web Push 订阅。endpoint 唯一：同一台设备重复订阅是覆盖，不是新增一行。
-- p256dh / auth 是浏览器给的加密材料，服务端只转发不解读。
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_ok_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_push_user
    ON push_subscriptions (user_id);
"""


def db_path() -> Path:
    root = Path(settings.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / DB_NAME


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    """补列 + 建表 + 必要时重建 wakes。**只在启动时跑一次**（见 `_connect`）。

    `CREATE TABLE IF NOT EXISTS` 对已存在的表一个字都不改，所以升级上来的库必须
    显式迁移。三代库：

    1. #10 之前：wakes 没有 source → ALTER 补列。**补列要在 executescript 之前**：
       SCHEMA 里那个 `idx_wakes_user_source_fired` 索引引用了 source，列还没有时
       整段脚本直接 `OperationalError: no such column: source`（实测）
    2. #10 之后、这次之前：status 的 CHECK 只认三态、没有 reason 列。SQLite 改不了
       CHECK，只能重建：改名 → 按新定义建 → 搬数据 → 删旧表。判据看 sqlite_master
       里存的建表 SQL 有没有 `'skipped'`。旧索引跟着旧表一起被 DROP，
       下面的 executescript 再按新表建回来
    3. 新库：表不存在 → 两步都跳过，建表时就是完整的
    4. 消息状态之前：messages 没有 status / reason → ALTER 补两列，老行全是 done。
       同样**要在 executescript 之前**：`idx_messages_user_status` 引用了 status
    """
    cur = await conn.execute("PRAGMA table_info(messages)")
    msg_cols = {row[1] for row in await cur.fetchall()}
    if msg_cols and "status" not in msg_cols:
        await conn.execute(
            "ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT 'done' "
            "CHECK (status IN ('pending', 'done', 'failed'))"
        )
        await conn.execute("ALTER TABLE messages ADD COLUMN reason TEXT")

    cur = await conn.execute("PRAGMA table_info(wakes)")
    cols = {row[1] for row in await cur.fetchall()}
    if cols and "source" not in cols:
        await conn.execute(
            "ALTER TABLE wakes ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
        )
        cols.add("source")

    if cols:
        cur = await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'wakes'"
        )
        row = await cur.fetchone()
        create_sql = (row[0] if row else "") or ""
        if "'skipped'" not in create_sql:
            await _rebuild_wakes(conn)

    await conn.executescript(SCHEMA)
    await conn.commit()


async def _rebuild_wakes(conn: aiosqlite.Connection) -> None:
    """把旧 wakes 按新定义重建一遍，数据原样搬过来（reason 全空）。"""
    await conn.execute("ALTER TABLE wakes RENAME TO wakes_old")
    await conn.execute(WAKES_TABLE_SQL)
    await conn.execute(
        "INSERT INTO wakes (id, user_id, wake_at, note, intent, status, source, "
        "created_at, fired_at) "
        "SELECT id, user_id, wake_at, note, intent, status, source, created_at, fired_at "
        "FROM wakes_old"
    )
    await conn.execute("DROP TABLE wakes_old")
    await conn.commit()


def _parse_ts(raw: str | None) -> datetime | None:
    """库里的戳（`...Z`）→ aware UTC datetime；坏值回 None。"""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def init_db() -> None:
    """启动时跑一次：WAL + 建表/迁移。

    WAL 是让「聊天在写、wake 同时也在写」不撞 `database is locked` 的前提：
    默认的 rollback journal 下读写互斥，两个连接一个在 commit 另一个就得等；
    WAL 下读不挡写、写不挡读，只剩写写互斥，靠 busy_timeout 排队。
    journal_mode 是**库文件**的属性，设一次永久生效。
    """
    async with aiosqlite.connect(db_path()) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await _ensure_schema(conn)
    swept = await sweep_pending_turns()
    if swept:
        log.warning("swept %s pending turns as failed:restart", swept)


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """开一个连接，用完关。

    **不再每次都跑 `_ensure_schema`**：那是 PRAGMA + 整段 executescript，每条查询
    付一遍，而且 executescript 自带 COMMIT 会打断事务语义。只有库文件不见了
    （运行中被人删掉）才补建一次——原来「survives wipe while up」那条保留。

    busy_timeout 是连接属性，每个连接都要设：写写相撞时等 5 秒再报 locked，
    而不是立刻炸。
    """
    path = db_path()
    fresh = not path.is_file()
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute("PRAGMA busy_timeout=5000")
        if fresh:
            await conn.execute("PRAGMA journal_mode=WAL")
            await _ensure_schema(conn)
        yield conn
    finally:
        await conn.close()


async def add_message(user_id: str, role: str, content: str) -> dict[str, Any]:
    async with _connect() as conn:
        cur = await conn.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        msg_id = cur.lastrowid
        await conn.commit()
        cur = await conn.execute(
            f"SELECT {MSG_COLS} FROM messages WHERE id = ?", (msg_id,)
        )
        row = await cur.fetchone()
        return dict(row)


async def list_messages(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """最近 limit 条，按时间正序返回。

    取最近要 `ORDER BY id DESC LIMIT ?` 再翻转——写成 ASC LIMIT 取到的是**最旧**
    的 limit 条：历史一过 limit，模型看到的窗口就冻在最早那段，新对话永远进不去
    prompt，前端也不再显示新消息。
    """
    async with _connect() as conn:
        cur = await conn.execute(
            f"SELECT {MSG_COLS} FROM messages "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def list_recent_turns(user_id: str, limit: int = 40) -> list[dict[str, Any]]:
    """给拼装用的最近轮次：**只有 done 的**。

    pending（正在跑的这句，由 Trigger 单独带）和 failed（没等到回复的句子）都
    不进历史——模型看见连续两条 user 句只会学着自言自语。过滤在 SQL 里做，
    limit 数的是能进 prompt 的行。
    """
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT id, role, content, created_at FROM messages "
            "WHERE user_id = ? AND status = 'done' AND role IN ('user', 'assistant') "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def history_for_llm(user_id: str, limit: int = 40) -> list[dict[str, str]]:
    """Recent turns for the model (role/content only; prefer list_recent_turns)."""
    turns = await list_recent_turns(user_id, limit=limit)
    return [{"role": t["role"], "content": t["content"]} for t in turns]


# --- 一轮聊天的状态机（挂在 user 行上）-------------------------------------


async def _get_message(conn: aiosqlite.Connection, msg_id: int) -> dict[str, Any] | None:
    cur = await conn.execute(f"SELECT {MSG_COLS} FROM messages WHERE id = ?", (msg_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def begin_user_turn(user_id: str, content: str) -> dict[str, Any]:
    """用户句先落库、标 pending，再去调模型。"""
    async with _connect() as conn:
        cur = await conn.execute(
            "INSERT INTO messages (user_id, role, content, status) "
            "VALUES (?, 'user', ?, 'pending')",
            (user_id, content),
        )
        msg_id = cur.lastrowid
        await conn.commit()
        row = await _get_message(conn, msg_id)
        assert row is not None
        return row


async def complete_turn_tx(user_msg_id: int, user_id: str, reply: str) -> dict[str, Any] | None:
    """回复落库：user 标 done + INSERT assistant，**一个事务**（同 fire_wake_tx）。

    `WHERE status = 'pending'` 兼做幂等：rowcount 为 0 = 这轮已经收过口或已被判
    failed，整个事务回滚、返回 None，一个字都不写。
    """
    async with _connect() as conn:
        cur = await conn.execute(
            "UPDATE messages SET status = 'done', reason = NULL "
            "WHERE id = ? AND user_id = ? AND status = 'pending'",
            (user_msg_id, user_id),
        )
        if cur.rowcount == 0:
            await conn.rollback()
            return None
        cur = await conn.execute(
            "INSERT INTO messages (user_id, role, content, status) "
            "VALUES (?, 'assistant', ?, 'done')",
            (user_id, reply),
        )
        msg_id = cur.lastrowid
        await conn.commit()
        return await _get_message(conn, msg_id)


async def fail_turn(user_msg_id: int, reason: str) -> bool:
    """模型挂了：pending → failed + reason。reason 必填，这就是留痕本身。"""
    async with _connect() as conn:
        cur = await conn.execute(
            "UPDATE messages SET status = 'failed', reason = ? "
            "WHERE id = ? AND status = 'pending'",
            (reason, user_msg_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def reopen_turn(user_msg_id: int, user_id: str) -> dict[str, Any]:
    """重发：failed → pending，**复用同一行**，不重插。

    只允许重发该用户**最后一条 user 句**：后面已经有新的一句时，这条的回复会
    排到新那轮之后，读起来串行。回 `{"ok": False, "detail": slug}`，slug 有三个：
    not_found / not_failed / not_latest，路由据此挑状态码。
    """
    async with _connect() as conn:
        row = await _get_message(conn, user_msg_id)
        if not row or row["user_id"] != user_id or row["role"] != "user":
            return {"ok": False, "detail": "not_found"}
        if row["status"] != "failed":
            return {"ok": False, "detail": "not_failed"}
        cur = await conn.execute(
            "SELECT id FROM messages WHERE user_id = ? AND role = 'user' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        latest = await cur.fetchone()
        if latest and int(latest["id"]) != int(user_msg_id):
            return {"ok": False, "detail": "not_latest"}
        cur = await conn.execute(
            "UPDATE messages SET status = 'pending', reason = NULL "
            "WHERE id = ? AND status = 'failed'",
            (user_msg_id,),
        )
        if cur.rowcount == 0:
            await conn.rollback()
            return {"ok": False, "detail": "not_failed"}
        await conn.commit()
        return {"ok": True, "message": await _get_message(conn, user_msg_id)}


async def sweep_pending_turns() -> int:
    """启动时跑：进程在「user 已落、回复未落」之间挂掉的轮，标 failed:restart。

    和 wake 重启 missed 不补发是同一条纪律——不替用户重发，给他一个重发按钮。
    """
    async with _connect() as conn:
        cur = await conn.execute(
            "UPDATE messages SET status = 'failed', reason = 'restart' "
            "WHERE status = 'pending'"
        )
        await conn.commit()
        return int(cur.rowcount or 0)


async def create_wake(
    user_id: str,
    wake_at: str,
    note: str | None = None,
    intent: str = "check_in",
    source: str = "manual",
) -> dict[str, Any]:
    src = source if source in ("manual", "auto") else "manual"
    async with _connect() as conn:
        cur = await conn.execute(
            "INSERT INTO wakes (user_id, wake_at, note, intent, status, source) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (user_id, wake_at, note, intent or "check_in", src),
        )
        wake_id = cur.lastrowid
        await conn.commit()
        cur = await conn.execute(
            f"SELECT {WAKE_COLS} "
            "FROM wakes WHERE id = ?",
            (wake_id,),
        )
        row = await cur.fetchone()
        return dict(row)


async def get_wake(wake_id: int) -> dict[str, Any] | None:
    async with _connect() as conn:
        cur = await conn.execute(
            f"SELECT {WAKE_COLS} "
            "FROM wakes WHERE id = ?",
            (wake_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_wakes(
    user_id: str,
    status: str | None = "pending",
    limit: int = 50,
    source: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    where = ["user_id = ?"]
    args: list[Any] = [user_id]
    if status:
        where.append("status = ?")
        args.append(status)
    if source:
        where.append("source = ?")
        args.append(source)
    # 查 pending 是「接下来会发生什么」，按时间正序；查全部是翻历史，最近的在前
    order = "wake_at ASC" if status == "pending" else "id DESC"
    args.append(limit)
    async with _connect() as conn:
        cur = await conn.execute(
            f"SELECT {WAKE_COLS} FROM wakes WHERE {' AND '.join(where)} "
            f"ORDER BY {order} LIMIT ?",
            tuple(args),
        )
        return [dict(r) for r in await cur.fetchall()]


async def fire_wake_tx(wake_id: int, user_id: str, text: str) -> dict[str, Any] | None:
    """开火落库：标 fired + 写那条 assistant，**一个事务**。

    以前是两个连接两步：先 add_message 再 mark_wake_fired。中间进程挂掉，消息
    落了、状态还是 pending，重启 `restore_pending_wakes` 再开一次火——用户收到
    两条一样的「我来看你啦」。这就是那个「外部已成功、本地未落库」的窗口，
    只不过外部是 messages 表。

    `WHERE status = 'pending'` 兼做幂等：rowcount 为 0 = 别人已经开过了（或被取消），
    整个事务回滚、返回 None，一个字都不写。
    """
    async with _connect() as conn:
        cur = await conn.execute(
            "UPDATE wakes SET status = 'fired', "
            "fired_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ? AND status = 'pending'",
            (wake_id,),
        )
        if cur.rowcount == 0:
            await conn.rollback()
            return None
        cur = await conn.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, 'assistant', ?)",
            (user_id, text),
        )
        msg_id = cur.lastrowid
        await conn.commit()
        cur = await conn.execute(
            f"SELECT {MSG_COLS} FROM messages WHERE id = ?", (msg_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def _close_wake(wake_id: int, status: str, reason: str | None) -> bool:
    if status not in ("cancelled", "skipped"):
        raise ValueError(f"bad terminal status: {status}")
    async with _connect() as conn:
        cur = await conn.execute(
            "UPDATE wakes SET status = ?, reason = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, reason, wake_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def mark_wake_cancelled(wake_id: int, reason: str | None = None) -> bool:
    """**人**取消的：wake_cancel、用户关掉随机。"""
    return await _close_wake(wake_id, "cancelled", reason)


async def mark_wake_skipped(wake_id: int, reason: str) -> bool:
    """**系统**没让它开：护栏挡了、停机过期、重挑。reason 必填，这就是留痕本身。"""
    return await _close_wake(wake_id, "skipped", reason)


async def wake_stats(user_id: str, *, days: int = 7, reply_hours: int = 6) -> dict[str, Any]:
    """PLAN §11 候选指标里「它先开口的接受率」：最近 `days` 天开过火的 wake，
    有多少条在 `reply_hours` 小时内等到了用户的下一句。

    数据本来就在库里（fired_at + 下一条 user 的 created_at），只是以前没人算。
    顺带把 skipped / cancelled 按 reason 分桶——护栏到底在挡什么，看这儿。
    """
    since = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT source, fired_at FROM wakes "
            "WHERE user_id = ? AND status = 'fired' AND fired_at >= ?",
            (user_id, since),
        )
        fired = [dict(r) for r in await cur.fetchall()]

        replied = 0
        by_source: dict[str, dict[str, int]] = {}
        for w in fired:
            src = w["source"] or "manual"
            bucket = by_source.setdefault(src, {"fired": 0, "replied": 0})
            bucket["fired"] += 1
            start = w["fired_at"]
            end_dt = _parse_ts(start)
            end = (
                (end_dt + timedelta(hours=reply_hours)).strftime("%Y-%m-%dT%H:%M:%S")
                if end_dt
                else start
            )
            cur = await conn.execute(
                "SELECT 1 FROM messages WHERE user_id = ? AND role = 'user' "
                "AND created_at > ? AND created_at < ? LIMIT 1",
                (user_id, start, end),
            )
            if await cur.fetchone():
                replied += 1
                bucket["replied"] += 1

        cur = await conn.execute(
            "SELECT status, COALESCE(reason, '') AS reason, COUNT(*) AS n FROM wakes "
            "WHERE user_id = ? AND status IN ('skipped', 'cancelled') AND created_at >= ? "
            "GROUP BY status, reason",
            (user_id, since),
        )
        closed = {
            f"{r['status']}:{r['reason'] or '-'}": int(r["n"]) for r in await cur.fetchall()
        }

    n = len(fired)
    return {
        "days": days,
        "reply_window_hours": reply_hours,
        "fired": n,
        "replied": replied,
        "acceptance": round(replied / n, 3) if n else None,
        "by_source": by_source,
        "closed": closed,
    }


async def count_pending_wakes(
    user_id: str | None = None,
    source: str | None = None,
) -> int:
    uid = user_id or settings.user_id
    async with _connect() as conn:
        if source:
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM wakes "
                "WHERE user_id = ? AND status = 'pending' AND source = ?",
                (uid, source),
            )
        else:
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM wakes "
                "WHERE user_id = ? AND status = 'pending'",
                (uid,),
            )
        row = await cur.fetchone()
        return int(row["n"] if row else 0)


# --- 随机醒来的护栏要问的三件事（#10）--------------------------------------
#
# 三个都只数 `source = 'auto'`：日上限 / 最小间隔管的是他自己起意来找你的次数，
# 不该被「你让他 9 点叫你起床」那种 manual 挤掉额度。


async def list_pending_auto_wakes(user_id: str) -> list[dict[str, Any]]:
    """待开火的随机醒来。正常只有 0 或 1 条（重挑前先把旧的作废）。"""
    async with _connect() as conn:
        cur = await conn.execute(
            f"SELECT {WAKE_COLS} "
            "FROM wakes WHERE user_id = ? AND status = 'pending' AND source = 'auto' "
            "ORDER BY wake_at ASC",
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def last_auto_fire_at(user_id: str) -> datetime | None:
    """上一次随机醒来真的开火是什么时候（最小间隔用）。"""
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT fired_at FROM wakes WHERE user_id = ? AND status = 'fired' "
            "AND source = 'auto' AND fired_at IS NOT NULL "
            "ORDER BY fired_at DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return _parse_ts(row["fired_at"] if row else None)


async def count_auto_fires_between(
    user_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> int:
    """[start, end) 内开火过几次随机醒来（日上限用）。

    比的是字符串：库里的戳是 `2026-09-10T06:03:12.345Z`，边界给到秒
    （`2026-09-10T00:00:00`）——同为零填充 ISO，字典序即时序，前缀短一截也不影响。
    """
    start_s = start_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    end_s = end_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM wakes WHERE user_id = ? AND status = 'fired' "
            "AND source = 'auto' AND fired_at IS NOT NULL "
            "AND fired_at >= ? AND fired_at < ?",
            (user_id, start_s, end_s),
        )
        row = await cur.fetchone()
        return int(row["n"] if row else 0)


async def last_user_message_at(user_id: str) -> datetime | None:
    """用户最后一次说话的时间（「刚聊过就别随机来」用）。"""
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT created_at FROM messages WHERE user_id = ? AND role = 'user' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return _parse_ts(row["created_at"] if row else None)


# --- push subscriptions ---------------------------------------------------


async def upsert_push_subscription(
    user_id: str,
    endpoint: str,
    p256dh: str,
    auth: str,
) -> dict[str, Any]:
    """同一个 endpoint 再订阅就覆盖——浏览器换 key 时 endpoint 常常不变。"""
    async with _connect() as conn:
        await conn.execute(
            """
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id = excluded.user_id,
                p256dh = excluded.p256dh,
                auth = excluded.auth
            """,
            (user_id, endpoint, p256dh, auth),
        )
        await conn.commit()
        cur = await conn.execute(
            "SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
        row = await cur.fetchone()
        return dict(row) if row else {}


async def list_push_subscriptions(user_id: str | None = None) -> list[dict[str, Any]]:
    uid = user_id or settings.user_id
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT * FROM push_subscriptions WHERE user_id = ? ORDER BY id",
            (uid,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def delete_push_subscription(endpoint: str) -> bool:
    """推送被 endpoint 拒收（404/410）时调用——订阅过期了，留着只会每次都失败。"""
    async with _connect() as conn:
        cur = await conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
        await conn.commit()
        return cur.rowcount > 0


async def mark_push_ok(endpoint: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE push_subscriptions "
            "SET last_ok_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE endpoint = ?",
            (endpoint,),
        )
        await conn.commit()


async def count_push_subscriptions(user_id: str | None = None) -> int:
    uid = user_id or settings.user_id
    async with _connect() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id = ?", (uid,)
        )
        row = await cur.fetchone()
        return int(row["n"] if row else 0)
