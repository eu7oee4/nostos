"""测试环境：**在 import app 之前**把环境钉死。

`app.config.settings` 在 import 时就实例化，而且会读 CWD 下的 `.env`（开发机上那份
有真 key、可能开着 PROACTIVE_ENABLED）。环境变量优先级高于 .env，所以这里先设。
"""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="nostos-tests-")
os.environ["DATA_DIR"] = _TMP
os.environ["LLM_API_KEY"] = ""
os.environ["PROACTIVE_ENABLED"] = "false"
os.environ["ACCESS_TOKEN"] = ""
os.environ["USER_ID"] = "local"
os.environ["TIMEZONE"] = "Asia/Shanghai"
os.environ["LOG_LEVEL"] = "DEBUG"

import pytest  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """每个测试一个干净的 data/：db、memories、prefs、persona 全落这儿。"""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path
