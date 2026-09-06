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
    handler: Handler | None = None


@dataclass
class Registry:
    tools: Dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    def enabled_tools(self) -> list[ToolSpec]:
        return [t for t in self.tools.values() if t.enabled or t.builtin]


registry = Registry()


def _noop(**kwargs: Any) -> dict:
    return {"ok": False, "detail": "scaffold stub"}


# Builtins (product itself) — registered enabled for later wiring
registry.register(
    ToolSpec(
        name="memory_read",
        description="Read memories for the current user",
        builtin=True,
        enabled=True,
        side_effect="read",
        handler=_noop,
    )
)
registry.register(
    ToolSpec(
        name="memory_write",
        description="Write a memory for the current user",
        builtin=True,
        enabled=True,
        side_effect="write",
        handler=_noop,
    )
)
registry.register(
    ToolSpec(
        name="alarm_set",
        description="Set an alarm / wake (proactive reach-out primitive)",
        builtin=True,
        enabled=True,
        side_effect="write",
        handler=_noop,
    )
)
