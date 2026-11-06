from __future__ import annotations

from typing import Any

from ..tool_context import ToolContext
from .base import BaseTool


class SkillTool(BaseTool):
    name = "skill"

    def schema(self) -> dict[str, Any]:
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
        if context.catalog is None:
            raise ValueError("skill catalog unavailable")
        return context.catalog.load(arguments["name"])
