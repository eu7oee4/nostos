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


def _noop(**kwargs: Any) -> dict:
    return {"ok": False, "detail": "scaffold stub"}


def _bootstrap() -> None:
    from app.nostools.memory_tools import (
        memory_list_handler,
        memory_read_handler,
        memory_write_handler,
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
            name="alarm_set",
            description="Set an alarm / wake (proactive reach-out primitive)",
            builtin=True,
            enabled=True,
            side_effect="write",
            parameters={
                "type": "object",
                "properties": {
                    "when": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
            handler=_noop,
        )
    )


_bootstrap()
