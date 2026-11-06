"""Built-in tools and backwards-compatible dispatch helpers.

Concrete tool modules must not import other concrete tool implementations.
"""

from __future__ import annotations

from typing import Any, Callable

from ..skills import SkillCatalog
from ..tool_context import Policy, ToolExecutionContext

__all__ = ["Policy", "execute", "schemas"]


def schemas() -> list[dict[str, Any]]:
    """Provide schemas functionality.

    Args:
        None.

    Returns:
        Independent list of OpenAI-compatible tool definitions.
    """
    from ..tool_registry import ToolRegistry

    return ToolRegistry.discover().schemas()


def execute(
    name: str,
    args: dict[str, Any],
    policy: Policy,
    confirm: Callable[[str], bool],
    catalog: SkillCatalog | None = None,
) -> dict[str, Any]:
    """Provide execute functionality.

    Args:
        name: Unique registered tool name requested by the model.
        args: Raw argument mapping to validate and dispatch.
        policy: User-configured workspace boundary and allowed capabilities.
        confirm: Callback returning whether the user approves an operation.
        catalog: Task skill catalog, or None when unavailable.

    Returns:
        JSON-serializable tool result; registry wrappers convert errors.
    """
    from ..tool_registry import ToolRegistry

    return ToolRegistry.discover().execute(
        name, args, ToolExecutionContext(policy, confirm, catalog)
    )
