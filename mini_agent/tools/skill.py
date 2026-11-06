from __future__ import annotations

from typing import Any

from ..tool_context import ToolExecutionContext
from .base import BaseTool


class SkillTool(BaseTool):
    """Load registered skill instructions using the current task catalog."""

    name = "skill"

    def schema(self) -> dict[str, Any]:
        """Describe the registered skill name required for on-demand loading.

        Args:
            None.

        Returns:
            One OpenAI-compatible function tool definition.
        """
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

    def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        """Load instructions from the task catalog without executing any scripts.

        Args:
            arguments: Tool argument mapping; validated before execution.
            context: Task policy, approval callback and optional skill catalog.

        Returns:
            JSON-serializable tool result; registry wrappers convert errors.
        """
        if context.catalog is None:
            raise ValueError("skill catalog unavailable")
        return context.catalog.load(arguments["name"])
