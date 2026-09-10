"""Correction prefs on disk: data/prefs.json.

纠偏层。PLAN §5.2③ 拍板「决定留存的不是初始化多好，是头三天纠偏多快」——
用户随口说「你别这么客气」「说短点」，下一轮就得变，而且隔几轮还在。

**为什么不走 memory_write**：那条会进召回块，位置在断点③之后、每轮可变，而且它的
身份是「关于用户的一条事实」，模型未必当指令执行。风格纠偏必须**常驻注入**——
检索式记忆捞不到它，等于没记（PLAN §4.2）。

**为什么是独立 system 块而不是并进 persona**：PLAN §4.2 的排布本来就是
`[块1] persona + 规矩` / `[块2] preferences（纠偏直写这区）`，断点②落在两者之间，
改 prefs 只炸②以后，①那个大头仍命中。DESIGN §2.5 留的「MVP 可先并进 persona 直写」
2026-09-09 改判：并进去意味着每次纠偏都要重写整个人格文件。

**为什么没有回执**（2026-09-09 机主拍板，推翻 PLAN §5.2③ 原来那句「让他看见改了什么」）：
伙伴不会说「记下了：跟你说话不用铺垫」——那是软件在弹提示。他就是直接变了。
前端一个字不露，后端 `nostos.prefs` 留日志，出口在菜单（`GET/DELETE /prefs`）。

文件里两段：

    style[]  模型可写的纠偏偏好，**只有这段进拼装**
    wake{}   随机醒来的护栏（安静时段 / 日上限 / 最小间隔 / 刚聊过），#10 填上了。
             **模型没有这段的入口**：它是用户的护栏，不是他能自己放宽的东西

两段同一个文件是故意的：#10 的护栏和这里的纠偏共用一套存储，别再起第二份
（`feat/random-wake` 那条分支上的 `schedule/prefs.py` 不要合过来）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.memory import safe_id

PREFS_NAME = "prefs.json"

# 这块是常驻注入的稳定前缀，不能无限长。超了让模型去覆盖已有的那条。
MAX_STYLE_ITEMS = 30
MAX_TEXT_CHARS = 200

log = logging.getLogger("nostos.prefs")


def prefs_path() -> Path:
    return Path(settings.data_dir) / PREFS_NAME


def _empty() -> dict[str, Any]:
    return {"style": [], "wake": {}}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict[str, Any]:
    """缺文件 / 坏 JSON 一律回空壳（抄 profile._raw_profile 的容错口径）。"""
    path = prefs_path()
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("prefs.json unreadable, treating as empty: %s", path)
        return _empty()
    if not isinstance(data, dict):
        return _empty()

    out = _empty()
    style = data.get("style")
    if isinstance(style, list):
        out["style"] = [
            item
            for item in style
            if isinstance(item, dict) and (item.get("id") or "").strip()
        ]
    wake = data.get("wake")
    if isinstance(wake, dict):
        out["wake"] = wake
    return out


def _save(data: dict[str, Any]) -> None:
    """临时文件 + os.replace：写一半断电不会留下半个 JSON。"""
    path = prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def list_style() -> list[dict[str, Any]]:
    """已记下的纠偏偏好，按写入顺序。"""
    return list(_load()["style"])


def write_style(pref_id: str, text: str) -> dict[str, Any]:
    """记下一条纠偏偏好；同 id 覆盖，不追加第二条。"""
    clean = (text or "").strip()
    if not clean:
        return {"ok": False, "detail": "text is empty"}
    if len(clean) > MAX_TEXT_CHARS:
        return {"ok": False, "detail": f"text too long (max {MAX_TEXT_CHARS})"}

    pid = safe_id(pref_id)
    data = _load()
    style = data["style"]

    for item in style:
        if item.get("id") == pid:
            item["text"] = clean
            item["updated_at"] = _now()
            _save(data)
            log.info("prefs style updated: %s = %s", pid, clean)
            return {"ok": True, "id": pid, "created": False}

    if len(style) >= MAX_STYLE_ITEMS:
        return {
            "ok": False,
            "detail": (
                f"too many prefs (max {MAX_STYLE_ITEMS}); "
                "overwrite an existing id instead"
            ),
        }

    style.append({"id": pid, "text": clean, "updated_at": _now()})
    _save(data)
    log.info("prefs style written: %s = %s", pid, clean)
    return {"ok": True, "id": pid, "created": True}


def delete_style(pref_id: str) -> bool:
    """隐私出口用（PLAN §4.2「用户可看可删」）。模型没有这个入口。"""
    pid = safe_id(pref_id)
    data = _load()
    style = data["style"]
    kept = [item for item in style if item.get("id") != pid]
    if len(kept) == len(style):
        return False
    data["style"] = kept
    _save(data)
    log.info("prefs style deleted: %s", pid)
    return True


def prefs_block() -> str | None:
    """System block for assembly. 没有任何一条 → None（整块省略，不留占位）。"""
    items = [
        (item.get("text") or "").strip()
        for item in _load()["style"]
    ]
    lines = [text for text in items if text]
    if not lines:
        return None
    body = "\n".join(f"- {text}" for text in lines)
    return f"【纠正过的说话方式】\n下面这些是对方亲口纠正过的，一直按这个来。\n{body}"


# --- wake 护栏（#10 随机醒来） ---------------------------------------------
#
# 护栏只是护栏，不是决策者（DESIGN §6）。这里的四条规矩管的是「随机醒来」这条
# 补充路径能不能开火；模型用 wake_set 定的时刻仍然照定、照到点执行。
#
# 唯一对 manual 生效的是 quiet_hours，而且只掐**出站推送**：安静时段里他照样
# 醒、照样生成、照样落进聊天记录，只是不顶到锁屏上。见 schedule/scheduler.py。

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

DEFAULT_WAKE: dict[str, Any] = {
    # 安静时段：跨夜写法（start > end）合法，见 policy._in_window
    "quiet_hours": {
        "enabled": True,
        "windows": [{"start": "23:00", "end": "08:00"}],
    },
    # 两次**随机**醒来之间至少隔多久
    "min_interval": {"enabled": True, "minutes": 240},
    # 每个本地日最多几次随机醒来
    "daily_cap": {"enabled": True, "max": 3},
    # 用户刚说过话，这段时间内不随机醒（正聊着还主动来是发癫）
    "recent_chat": {"enabled": True, "minutes": 45},
    # 随机醒来本身的开关 + 往后最多摸多远挑那个点
    "random": {"enabled": True, "max_horizon_hours": 18},
}


def _clamp(value: Any, lo: int, hi: int, fallback: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return fallback


def _normalize_wake(raw: Any) -> dict[str, Any]:
    """脏数据一律回默认值，不抛——这文件用户会手改。"""
    out = deepcopy(DEFAULT_WAKE)
    if not isinstance(raw, dict):
        return out

    qh = raw.get("quiet_hours")
    if isinstance(qh, dict):
        out["quiet_hours"]["enabled"] = bool(qh.get("enabled", True))
        windows = qh.get("windows")
        if isinstance(windows, list):
            cleaned = []
            for w in windows:
                if not isinstance(w, dict):
                    continue
                start = str(w.get("start") or "").strip()
                end = str(w.get("end") or "").strip()
                # 格式不对就整条丢掉。留着半条的后果是安静时段静默失效，
                # 而失效的表现是「凌晨被推醒」——宁可日志里吵一句。
                if _HHMM.match(start) and _HHMM.match(end):
                    cleaned.append({"start": start, "end": end})
                else:
                    log.warning("prefs wake: dropping bad quiet window %r", w)
            out["quiet_hours"]["windows"] = cleaned

    mi = raw.get("min_interval")
    if isinstance(mi, dict):
        out["min_interval"]["enabled"] = bool(mi.get("enabled", True))
        if mi.get("minutes") is not None:
            out["min_interval"]["minutes"] = _clamp(mi["minutes"], 30, 24 * 60, 240)

    dc = raw.get("daily_cap")
    if isinstance(dc, dict):
        out["daily_cap"]["enabled"] = bool(dc.get("enabled", True))
        if dc.get("max") is not None:
            out["daily_cap"]["max"] = _clamp(dc["max"], 1, 20, 3)

    rc = raw.get("recent_chat")
    if isinstance(rc, dict):
        out["recent_chat"]["enabled"] = bool(rc.get("enabled", True))
        if rc.get("minutes") is not None:
            out["recent_chat"]["minutes"] = _clamp(rc["minutes"], 5, 24 * 60, 45)

    rnd = raw.get("random")
    if isinstance(rnd, dict):
        out["random"]["enabled"] = bool(rnd.get("enabled", True))
        if rnd.get("max_horizon_hours") is not None:
            out["random"]["max_horizon_hours"] = _clamp(
                rnd["max_horizon_hours"], 1, 72, 18
            )

    return out


def load_wake() -> dict[str, Any]:
    """护栏设置，缺项补默认。**不写盘**——没设置过就跑默认值，文件保持干净。"""
    return _normalize_wake(_load()["wake"])


def save_wake(patch: dict[str, Any]) -> dict[str, Any]:
    """合并写入（不是整体替换）。

    PUT 只带 `{"daily_cap": {"max": 1}}` 时，其他三条保持用户原来设的，不被默认值
    冲掉——这是设置页按单个开关提交时唯一不会误伤的语义。
    """
    data = _load()
    current = _normalize_wake(data["wake"])
    merged = deepcopy(current)
    if isinstance(patch, dict):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
    data["wake"] = _normalize_wake(merged)
    _save(data)
    log.info("prefs wake saved: %s", json.dumps(data["wake"], ensure_ascii=False))
    return data["wake"]
