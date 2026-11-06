from __future__ import annotations

from typing import Any

from ..tool_context import ToolContext
from .base import BaseTool


class SkillTool(BaseTool):
    """Load registered skill instructions using the current task catalog."""

    name = "skill"

    def schema(self) -> dict[str, Any]:
        """Describe the registered skill name required for on-demand loading."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Load full instructions for a name in the available skill catalog. "
                    "Resources are relative to the returned base_directory; "
                    "no scripts are executed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        """Load instructions from the task catalog without executing any scripts."""
        if context.catalog is None:
            raise ValueError("skill catalog unavailable")
        return context.catalog.load(arguments["name"])
