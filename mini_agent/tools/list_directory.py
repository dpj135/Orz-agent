from __future__ import annotations

from typing import Any

from ..tool_context import DIRECTORY_ENTRY_LIMIT, ToolContext
from .base import BaseTool


class ListDirectoryTool(BaseTool):
    """List immediate workspace directory entries without recursive traversal."""

    name = "list_directory"

    def schema(self) -> dict[str, Any]:
        """Describe the workspace directory path argument."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "List entries in a workspace directory.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        """Check the directory path and return sorted names up to the entry cap."""
        path = context.policy.path(arguments["path"])
        if not path.is_dir():
            raise FileNotFoundError(arguments["path"])
        return {
            "ok": True,
            "entries": sorted(p.name for p in path.iterdir())[:DIRECTORY_ENTRY_LIMIT],
        }
