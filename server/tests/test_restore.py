"""重启时过期的 wake 怎么分：宽限内补开，超了记 missed。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schedule.scheduler import MISFIRE_GRACE_SECONDS, classify_restore

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def test_future_wake_is_armed():
    assert classify_restore(NOW + timedelta(hours=1), NOW) == "arm"


def test_overdue_within_grace_is_armed():
    assert classify_restore(NOW - timedelta(seconds=MISFIRE_GRACE_SECONDS - 1), NOW) == "arm"


def test_overdue_past_grace_is_missed():
    assert classify_restore(NOW - timedelta(seconds=MISFIRE_GRACE_SECONDS), NOW) == "missed"
    assert classify_restore(NOW - timedelta(days=3), NOW) == "missed"


def test_grace_is_a_parameter():
    assert classify_restore(NOW - timedelta(seconds=10), NOW, grace_seconds=5) == "missed"
    assert classify_restore(NOW - timedelta(seconds=10), NOW, grace_seconds=60) == "arm"
