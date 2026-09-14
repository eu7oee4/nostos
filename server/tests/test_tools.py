"""动作执行入口：side_effect 在这儿生效。"""

from __future__ import annotations

import pytest

from app import chat_loop
from app.nostools.registry import ToolSpec, registry


def test_side_effect_must_be_known():
    with pytest.raises(ValueError):
        ToolSpec(name="x", description="", side_effect="nuke")


@pytest.mark.anyio
async def test_outbound_tool_is_refused_before_running(monkeypatch):
    called: list[dict] = []
    registry.register(
        ToolSpec(
            name="send_mail_test",
            description="test only",
            builtin=True,
            enabled=True,
            side_effect="outbound",
            handler=lambda **kw: called.append(kw) or {"ok": True},
        )
    )
    monkeypatch.setattr(chat_loop, "CHAT_TOOL_NAMES", [*chat_loop.CHAT_TOOL_NAMES, "send_mail_test"])
    try:
        out = await chat_loop._run_tool("send_mail_test", {"to": "someone"})
    finally:
        registry.tools.pop("send_mail_test", None)
    assert out["ok"] is False and "approval" in out["detail"]
    assert called == []


@pytest.mark.anyio
async def test_unknown_and_unlisted_tools(monkeypatch):
    assert (await chat_loop._run_tool("nope", {}))["ok"] is False
    monkeypatch.setattr(chat_loop, "CHAT_TOOL_NAMES", [])
    assert (await chat_loop._run_tool("memory_list", {}))["detail"] == "tool not available: memory_list"


@pytest.mark.anyio
async def test_write_tool_runs_and_returns_dict(data_dir):
    out = await chat_loop._run_tool("memory_write_item", {"name": "hometown", "content": "上海"})
    assert out["ok"] is True and out["id"] == "hometown" and out["kind"] == "item"
    assert (data_dir / "memories" / "local" / "hometown.md").is_file()


@pytest.mark.anyio
async def test_feel_tool_needs_intensity(data_dir):
    out = await chat_loop._run_tool(
        "memory_write_feel", {"name": "mom-tension", "content": "和妈妈长时间相处会紧张"}
    )
    assert out["ok"] is False and "intensity" in out["detail"]
    out = await chat_loop._run_tool(
        "memory_write_feel",
        {"name": "mom-tension", "content": "和妈妈长时间相处会紧张", "intensity": "mid"},
    )
    assert out["ok"] is True and out["kind"] == "feel" and out["intensity"] == "mid"


@pytest.mark.anyio
async def test_legacy_memory_write_still_runs_but_is_not_offered(data_dir):
    """老名字一个版本周期内还能执行（当 item），但不在给模型看的那套里。"""
    out = await chat_loop._run_tool("memory_write", {"name": "hometown", "content": "上海"})
    assert out["ok"] is True and out["kind"] == "item"
    offered = {t["function"]["name"] for t in registry.openai_tools(chat_loop.CHAT_TOOL_NAMES)}
    assert "memory_write" not in offered
    assert {"memory_write_item", "memory_write_feel"} <= offered


def test_no_memory_delete_tool_for_the_model():
    assert "memory_delete" not in registry.tools
    assert not any("delete" in n and n.startswith("memory") for n in chat_loop.CHAT_TOOL_NAMES)


@pytest.mark.anyio
async def test_bad_arguments_surface_to_model():
    out = await chat_loop._run_tool("memory_read", {"bogus": 1, "name": 2, "extra": None})
    # 传错的参数不炸进程，回给模型
    assert isinstance(out, dict) and "ok" in out
