from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict


Handler = Callable[..., Awaitable[Any] | Any]

# 工具跑一次会碰到什么。执行入口（chat_loop._run_tool）认这个字段：
#   none / read   只看，随便调
#   write         改本机数据（记忆、纠偏、wake），每次调用记 INFO 留痕
#   outbound      往外面发东西（邮件、给别人发消息）。**人批之前不许跑**——
#                 现在没有批准通道，所以一律拒绝并记 WARNING。这是 ARCHITECTURE
#                 「出站副作用：人批 + 留痕」那条的 stub，等第一个真 outbound
#                 工具进来时把批准通道接到这里，别绕过去
SIDE_EFFECTS = ("none", "read", "write", "outbound")


@dataclass
class ToolSpec:
    name: str
    description: str
    builtin: bool = False
    enabled: bool = False
    side_effect: str = "none"  # 见 SIDE_EFFECTS
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: Handler | None = None

    def __post_init__(self) -> None:
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError(
                f"tool {self.name}: side_effect must be one of {SIDE_EFFECTS}, "
                f"got {self.side_effect!r}"
            )


@dataclass
class Registry:
    tools: Dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    def enabled_tools(self) -> list[ToolSpec]:
        return [t for t in self.tools.values() if t.enabled or t.builtin]

    def openai_tools(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        """OpenAI-compatible tools payload for chat completions."""
        specs = self.enabled_tools()
        if names is not None:
            allow = set(names)
            specs = [t for t in specs if t.name in allow]
        out: list[dict[str, Any]] = []
        for t in specs:
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters
                        or {"type": "object", "properties": {}},
                    },
                }
            )
        return out


registry = Registry()


def _bootstrap() -> None:
    from app.nostools.memory_tools import (
        memory_list_handler,
        memory_read_handler,
        memory_write_feel_handler,
        memory_write_handler,
        memory_write_item_handler,
    )
    from app.nostools.prefs_tools import (
        prefs_list_handler,
        prefs_write_handler,
    )
    from app.nostools.wake_tools import (
        wake_cancel_handler,
        wake_list_handler,
        wake_set_handler,
    )

    registry.register(
        ToolSpec(
            name="memory_list",
            description="List the user's long-term memories (id + title).",
            builtin=True,
            enabled=True,
            side_effect="read",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=memory_list_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="memory_read",
            description="Read one memory by id/name.",
            builtin=True,
            enabled=True,
            side_effect="read",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Memory id, e.g. name, hometown, preferences",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=memory_read_handler,
        )
    )
    # 记忆写入拆两路（Notion「记忆系统设计对照」第 ⑫ 点）。描述文本是唯一的杠杆
    # （issue #9 的教训），所以两条描述各自把触发例句写满，别合并成一个带 kind 参数的。
    _memory_write_params = {
        "name": {
            "type": "string",
            "description": "Short stable id slug, e.g. mom-visit, hometown, job",
        },
        "title": {
            "type": "string",
            "description": "Human title, optional",
        },
        "content": {
            "type": "string",
            "description": "One or two short lines, in Chinese, in the user's own terms",
        },
    }
    registry.register(
        ToolSpec(
            name="memory_write_item",
            # 例句是唯一的杠杆，而且只对**长得像例句的**句子有效（eval/README.md
            # 「事件探针」：留出集上改描述前后都是三成）。所以这里堆的是这个用户
            # 真会说的话——实习 / 方向 / 在投什么——不是通用规则。通用的那步靠兜底。
            description=(
                "Record a lasting FACT about the user's life the moment it comes up: where they "
                "work, study or intern and since when; what they do there; what they are planning, "
                "applying to or aiming for; people, pets, places, dates, things that happened or "
                "will happen. Triggers include 「我在滨江一个AI短剧公司实习，刚来一周多」"
                "「我想往AI产品经理方向做」「在投另一家公司」「在磨简历」「我妈下周来杭州」"
                "「我换工作了」「我猫叫团子」「我住在上海」. "
                "It counts even when it is a short answer to YOUR question (你问「在哪实习」"
                "她答「滨江 一个AI短剧公司」 — record it) and even when said in passing. "
                "When in doubt, record: a missed fact is gone for good, a redundant one just "
                "overwrites. Same id overwrites, so use a short stable id (internship, "
                "career-goal, job-hunt, mom-visit, cat). Facts only — what the user FEELS about "
                "it goes to memory_write_feel, in a separate call. Do not announce that you wrote it."
            ),
            builtin=True,
            enabled=True,
            side_effect="write",
            parameters={
                "type": "object",
                "properties": dict(_memory_write_params),
                "required": ["name", "content"],
                "additionalProperties": False,
            },
            handler=memory_write_item_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="memory_write_feel",
            description=(
                "Call this EVERY time the user reveals how they FEEL about "
                "something that will still matter later — nervous, afraid, tired of, "
                "excited, sad, guilty, resentful, attached, dreading, looking forward. "
                "Triggers include 「有点紧张」「一想到…就烦」「我挺怕…的」「特别在意」"
                "「舍不得」「说不上来为什么就是难受」「其实我挺开心的」. "
                "A feeling is not a fact: 「我妈下周来」 is memory_write_item; "
                "「和我妈长时间相处会紧张」 is this tool. Often one message needs both, "
                "in two separate calls. "
                "intensity: low = passing / mild, mid = it clearly weighs on them, "
                "high = it dominates how they feel right now. "
                "Without this call the feeling is gone once the conversation moves on. "
                "Do not announce that you wrote it; just respond to them."
            ),
            builtin=True,
            enabled=True,
            side_effect="write",
            parameters={
                "type": "object",
                "properties": {
                    **_memory_write_params,
                    "intensity": {
                        "type": "string",
                        "enum": ["low", "mid", "high"],
                        "description": "How strong the feeling is: low / mid / high",
                    },
                },
                "required": ["name", "content", "intensity"],
                "additionalProperties": False,
            },
            handler=memory_write_feel_handler,
        )
    )
    # 旧名字，保留一个版本周期当 item 的别名。能执行（chat_loop 认它），但不在
    # 给模型看的 CHAT_TOOL_NAMES 里。
    registry.register(
        ToolSpec(
            name="memory_write",
            description="Deprecated alias of memory_write_item.",
            builtin=True,
            enabled=True,
            side_effect="write",
            parameters={
                "type": "object",
                "properties": dict(_memory_write_params),
                "required": ["name", "content"],
                "additionalProperties": False,
            },
            handler=memory_write_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="prefs_write",
            description=(
                "Call this EVERY time the user says anything about HOW you talk "
                "— length, tone, politeness, preamble, asking questions back, "
                "emoji, punctuation, what you call them. "
                "Triggers include 「说短点」「你别这么客气」「别老反问我」"
                "「别用感叹号」「说话别这么正式」. "
                "Without this call the correction is forgotten as soon as the "
                "conversation window moves on — saying you will remember is not "
                "remembering. "
                "Store one short standing instruction to yourself; same id "
                "overwrites (short stable ids: brevity, no-preamble, tone). "
                "This is not a fact about the user — memory_write is for facts."
            ),
            builtin=True,
            enabled=True,
            side_effect="write",
            parameters={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Short stable slug, e.g. brevity, no-preamble",
                    },
                    "text": {
                        "type": "string",
                        "description": (
                            "One short line, phrased as an instruction to you, "
                            "e.g. 跟她说话不用铺垫，直接说"
                        ),
                    },
                },
                "required": ["id", "text"],
                "additionalProperties": False,
            },
            handler=prefs_write_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="prefs_list",
            description=(
                "List the speaking-style corrections already recorded (id + text). "
                "Check here before writing so you overwrite instead of piling on."
            ),
            builtin=True,
            enabled=True,
            side_effect="read",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=prefs_list_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="wake_set",
            description=(
                "Schedule a proactive wake: nostos will come find the user later "
                "with an in-app message. delay_seconds = relative from now "
                "(prefer for tests); wake_at = absolute ISO-8601 UTC. "
                "Requires PROACTIVE_ENABLED=true."
            ),
            builtin=True,
            enabled=True,
            side_effect="write",
            parameters={
                "type": "object",
                "properties": {
                    "delay_seconds": {
                        "type": "number",
                        "description": "Relative delay in seconds from now (good for local tests)",
                    },
                    "wake_at": {
                        "type": "string",
                        "description": "Absolute ISO-8601 UTC time to wake, e.g. 2026-09-07T04:00:00Z",
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional fixed line; if omitted, generate at wake time",
                    },
                    "intent": {
                        "type": "string",
                        "description": "Wake intent when note is empty, default check_in",
                    },
                },
                "additionalProperties": False,
            },
            handler=wake_set_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="wake_list",
            description="List pending wakes for the current user.",
            builtin=True,
            enabled=True,
            side_effect="read",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=wake_list_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="wake_cancel",
            description="Cancel a pending wake by id.",
            builtin=True,
            enabled=True,
            side_effect="write",
            parameters={
                "type": "object",
                "properties": {
                    "wake_id": {"type": "integer", "description": "Wake id from wake_list"}
                },
                "required": ["wake_id"],
                "additionalProperties": False,
            },
            handler=wake_cancel_handler,
        )
    )


_bootstrap()
