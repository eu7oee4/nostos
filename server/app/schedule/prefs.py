"""User wake preferences: data/wake_prefs.json."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.config import settings

PREFS_NAME = "wake_prefs.json"

MIN_INTERVAL_MIN = 30
MIN_INTERVAL_MAX = 24 * 60  # 24h
MIN_INTERVAL_STEP = 30

DEFAULT_PREFS: dict[str, Any] = {
    "timezone": "Asia/Shanghai",
    "proactive_enabled": False,
    "quiet_hours": {
        "enabled": True,
        "windows": [{"start": "23:00", "end": "08:00"}],
    },
    "min_interval": {
        "enabled": True,
        "minutes": 240,
    },
    "daily_cap": {
        "enabled": True,
        "max": 3,
    },
    "recent_chat": {
        "enabled": True,
        "minutes": 45,
    },
    "random": {
        "enabled": True,
        "max_horizon_hours": 18,
    },
    # 0 = off; optional safety patrol, not the main path
    "policy_patrol_minutes": 0,
}


def prefs_path() -> Path:
    root = Path(settings.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / PREFS_NAME


def _clamp_interval(minutes: int) -> int:
    m = int(minutes)
    m = max(MIN_INTERVAL_MIN, min(MIN_INTERVAL_MAX, m))
    # snap to step
    stepped = MIN_INTERVAL_MIN + ((m - MIN_INTERVAL_MIN) // MIN_INTERVAL_STEP) * MIN_INTERVAL_STEP
    return max(MIN_INTERVAL_MIN, min(MIN_INTERVAL_MAX, stepped))


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(DEFAULT_PREFS)
    if not isinstance(data, dict):
        return out

    if isinstance(data.get("timezone"), str) and data["timezone"].strip():
        out["timezone"] = data["timezone"].strip()
    if "proactive_enabled" in data:
        out["proactive_enabled"] = bool(data["proactive_enabled"])

    qh = data.get("quiet_hours") or {}
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
                if len(start) == 5 and len(end) == 5:
                    cleaned.append({"start": start, "end": end})
            out["quiet_hours"]["windows"] = cleaned

    mi = data.get("min_interval") or {}
    if isinstance(mi, dict):
        out["min_interval"]["enabled"] = bool(mi.get("enabled", True))
        if mi.get("minutes") is not None:
            out["min_interval"]["minutes"] = _clamp_interval(int(mi["minutes"]))

    dc = data.get("daily_cap") or {}
    if isinstance(dc, dict):
        out["daily_cap"]["enabled"] = bool(dc.get("enabled", True))
        if dc.get("max") is not None:
            out["daily_cap"]["max"] = max(1, min(20, int(dc["max"])))

    rc = data.get("recent_chat") or {}
    if isinstance(rc, dict):
        out["recent_chat"]["enabled"] = bool(rc.get("enabled", True))
        if rc.get("minutes") is not None:
            out["recent_chat"]["minutes"] = max(5, min(24 * 60, int(rc["minutes"])))

    rnd = data.get("random") or {}
    if isinstance(rnd, dict):
        out["random"]["enabled"] = bool(rnd.get("enabled", True))
        if rnd.get("max_horizon_hours") is not None:
            out["random"]["max_horizon_hours"] = max(
                1, min(72, int(rnd["max_horizon_hours"]))
            )

    if data.get("policy_patrol_minutes") is not None:
        out["policy_patrol_minutes"] = max(0, min(120, int(data["policy_patrol_minutes"])))

    return out


def load_prefs() -> dict[str, Any]:
    path = prefs_path()
    if not path.is_file():
        seeded = deepcopy(DEFAULT_PREFS)
        seeded["proactive_enabled"] = bool(settings.proactive_enabled)
        save_prefs(seeded)
        return seeded
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw = {}
    return _normalize(raw)


def save_prefs(data: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(data)
    path = prefs_path()
    path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return normalized


def proactive_on(prefs: dict[str, Any] | None = None) -> bool:
    p = prefs or load_prefs()
    return bool(p.get("proactive_enabled"))
