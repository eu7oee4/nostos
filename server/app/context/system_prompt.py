"""Fixed system prompt (separate from persona). Loaded from prompts/system.md."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SYSTEM_MD = _PROMPTS_DIR / "system.md"

_FALLBACK = (
    "你是 nostos，用户的 AI 伙伴（不是助手工具箱）。说话自然、简短。\n"
    "你有长期记忆（markdown 文件）。用户说出值得长期记住的事实时，"
    "用 memory_write 写入（短 id，如 name / hometown / preferences）；"
    "需要核对细节时用 memory_read / memory_list。\n"
    "你可以预约主动来找用户：wake_set（delay_seconds 为相对延迟；"
    "wake_at 为绝对 ISO 时间）。可带 note 或 intent。"
    "wake_list / wake_cancel 查看或取消。主动触达需 PROACTIVE_ENABLED。\n"
    "不要把工具过程念给用户听；不要编造未写入的记忆。"
    "闲聊不必强行写记忆或设 wake。\n"
    "安全：拒绝涉及未成年人的色情或性剥削内容；不协助违法伤害。"
)


def get_system_prompt() -> str:
    if _SYSTEM_MD.is_file():
        try:
            text = _SYSTEM_MD.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    return _FALLBACK
