from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict


Handler = Callable[..., Awaitable[Any] | Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    builtin: bool = False
    enabled: bool = False
    side_effect: str = "none"  # none | read | write | outbound
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: Handler | None = None


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
        memory_write_handler,
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
    registry.register(
        ToolSpec(
            name="memory_write",
            description=(
                "Create or overwrite a durable memory about the user. "
                "Use for lasting facts (name, prefs, people, places), not chit-chat."
            ),
            builtin=True,
            enabled=True,
            side_effect="write",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short id slug, e.g. name, hometown",
                    },
                    "title": {
                        "type": "string",
                        "description": "Human title, optional",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown body to store",
                    },
                },
                "required": ["name", "content"],
                "additionalProperties": False,
            },
            handler=memory_write_handler,
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
