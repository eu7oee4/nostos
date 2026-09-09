"""Single prompt assembly pipeline (cache-disciplined order).

拼装的形状（2026-09-08 定）：**当前轮和历史轮长得一样**，差别只有一行。

    历史（冻结，写下就不再改）           当前轮（每轮重算）
    【09-08 周二 11:38 中午】           【09-08 周二 11:42 中午】
    地址不知道怎么写呀                  【距离上一条消息，过了 4 分钟】
                                       那这个呢

为什么要长得一样：模型会模仿**只在当前轮出现的那套格式**——它每轮出现在紧贴
生成边界的位置，看多了就变成"轮次开始的仪式"，于是他说完自己那句之后，顺手把
下一条 user 轮也写出来。cassette 2026-09-07 / 09-08 各实测到一次，形状是
`user【09-08 周二 11:38】…眠眠：<他编的下一句>`（角色标签来自引擎缝隙学舌，
框的内容来自注入）。历史里全是同一个形状，就没有"仪式"可学。

assistant 行**不打戳**：user 有戳 / assistant 没戳的不对称，是最强的"别给自己
加框"信号——1990 份 cassette transcript 里，他一次都没给自己的话加过戳。同款
拍板见 cassette `chat_loop.py:947`（那边的理由是"第一人称、不加框"）。

代价：历史行剥掉「距离」那一行 = 上一轮字节变了，不是纯追加。断点落在倒数第二
条 user 轮，每轮重付「上一轮 user + 上一轮 assistant + 本轮」。前面全命中。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.config import settings
from app.context.system_prompt import get_system_prompt
from app.persona import load_persona
from app.profile import profile_block

_WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_EMPTY_RECALL = "（暂无长期记忆）"


def _daypart(hour: int) -> str:
    if hour < 5:
        return "凌晨"
    if hour < 8:
        return "清晨"
    if hour < 11:
        return "上午"
    if hour < 13:
        return "中午"
    if hour < 17:
        return "下午"
    if hour < 19:
        return "傍晚"
    if hour < 23:
        return "晚上"
    return "深夜"


@dataclass(frozen=True)
class Trigger:
    """Current-turn trigger (not part of frozen dialog history)."""

    kind: Literal["user", "wake"]
    text: str = ""
    note: str | None = None
    intent: str | None = None


def _zone(tz_name: str | None = None) -> ZoneInfo:
    return ZoneInfo(tz_name or settings.timezone)


def _parse_created_at(raw: str | datetime | None) -> datetime:
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    if not raw:
        return datetime.now(timezone.utc)
    s = str(raw).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_stamp(created_at: str | datetime | None, tz_name: str | None = None) -> str:
    """冻结戳：`MM-DD 周X HH:MM 时段`。历史行和当前轮同一个格式，全场只有这一种。

    带星期：从日期反推星期模型不可靠，而"周末还是工作日"是他判断该不该打扰的
    依据。带时段词：24 小时制虽然读得出，但"11:38 中午"比"11:38"更直接落到
    该用什么语气上。年份逐条重复零信息，不带。
    """
    tz = _zone(tz_name)
    dt = _parse_created_at(created_at).astimezone(tz)
    return (f"{dt.strftime('%m-%d')} {_WEEKDAYS_ZH[dt.weekday()]} "
            f"{dt.strftime('%H:%M')} {_daypart(dt.hour)}")


def format_gap(seconds: float) -> str:
    """秒差转人话。只出现在当前轮，落成历史时剥掉。"""
    sec = int(seconds)
    if sec < 60:
        return "不到 1 分钟"
    minutes = sec // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时"
    return f"{hours // 24} 天"


def _render_dialog_row(
    row: dict[str, Any],
    tz_name: str,
) -> dict[str, str] | None:
    role = row.get("role")
    content = row.get("content") or ""
    if role == "user":
        # 不带「称呼：」前缀——「名字：台词」是剧本格式，剧本天然招续写，
        # 而说话人本来就由 role 字段承担，正文里再写一遍是白送一个模仿目标。
        stamp = format_stamp(row.get("created_at"), tz_name)
        return {"role": "user", "content": f"【{stamp}】\n{content}"}
    if role == "assistant":
        # 不打戳、不加框——他自己的话在他眼里就是第一人称的记忆。
        return {"role": "assistant", "content": content}
    return None


def _trigger_message(
    trigger: Trigger,
    *,
    tz_name: str,
    now: datetime,
    gap_seconds: float | None,
) -> dict[str, str]:
    lines = [f"【{format_stamp(now, tz_name)}】"]
    if gap_seconds is not None:
        lines.append(f"【距离上一条消息，过了 {format_gap(gap_seconds)}】")

    if trigger.kind == "user":
        lines.append(trigger.text or "")
        return {"role": "user", "content": "\n".join(lines)}

    # wake (7b)：不是她说话，用〔〕框住——user 槽里除了她的原话就只有这一种东西，
    # 得一眼分得开，免得重铸之后被读成"她说了句奇怪的话"。
    parts = ["没人找你，是你自己到点醒过来的。"]
    note = (trigger.note or "").strip()
    intent = (trigger.intent or "").strip()
    extra = (trigger.text or "").strip()
    if note:
        parts.append(f"你留的话：{note}")
    elif intent:
        parts.append(f"你留的意图：{intent}")
    if extra and extra not in (note, intent):
        parts.append(extra)
    lines.append("〔" + " ".join(parts) + "〕")
    return {"role": "user", "content": "\n".join(lines)}


def build_messages(
    *,
    user_id: str,
    history_rows: list[dict[str, Any]],
    recall: str,
    trigger: Trigger,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Assemble LLM messages in cache-stable order.

    history_rows = prior turns only (id/role/content/created_at);
    current user/wake text comes from trigger (no double user line).
    user_id is reserved for future per-user assets; profile/persona are
    single-tenant paths under data_dir today.
    """
    _ = user_id  # single-tenant for now; keep signature for wake/multi-user
    tz_name = settings.timezone
    at = now or datetime.now(timezone.utc)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": get_system_prompt()},
    ]

    block = profile_block()
    if block:
        messages.append({"role": "system", "content": block})

    persona = load_persona()
    if persona:
        messages.append(
            {
                "role": "system",
                "content": f"【伙伴人格 / persona】\n{persona}",
            }
        )

    for row in history_rows:
        rendered = _render_dialog_row(row, tz_name)
        if rendered:
            messages.append(rendered)

    recall_clean = (recall or "").strip()
    if recall_clean and recall_clean != _EMPTY_RECALL:
        messages.append(
            {
                "role": "system",
                "content": f"现在浮现在你脑海里的记忆有：\n{recall_clean}",
            }
        )

    # 「现在几点」＝当前轮那条戳（PLAN §6.3：时间锚就是最新一条消息自带的戳）。
    # 原来那条独立的「此刻：…」后缀去掉了：信息重复，而且它是又一个"只在当前轮
    # 出现"的块。
    gap: float | None = None
    if history_rows:
        last_at = _parse_created_at(history_rows[-1].get("created_at"))
        delta = (at - last_at).total_seconds()
        if delta >= 0:
            gap = delta

    messages.append(
        _trigger_message(trigger, tz_name=tz_name, now=at, gap_seconds=gap)
    )
    return messages
