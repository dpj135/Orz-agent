"""Built-in tools and backwards-compatible dispatch helpers.

Concrete tool modules must not import other concrete tool implementations.
"""

from __future__ import annotations

from typing import Any, Callable

from ..skills import SkillCatalog
from ..tool_context import Policy, ToolContext

__all__ = ["Policy", "execute", "schemas"]


def schemas() -> list[dict[str, Any]]:
    from ..tool_registry import ToolRegistry

    return ToolRegistry.discover().schemas()


def execute(
    name: str,
    args: dict[str, Any],
    policy: Policy,
    confirm: Callable[[str], bool],
    catalog: SkillCatalog | None = None,
) -> dict[str, Any]:
    from ..tool_registry import ToolRegistry

    return ToolRegistry.discover().execute(name, args, ToolContext(policy, confirm, catalog))
