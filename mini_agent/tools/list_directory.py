from __future__ import annotations

from typing import Any

from ..text_safety import replace_unpaired_surrogates
from ..tool_execution_context import DIRECTORY_ENTRY_LIMIT, ToolExecutionContext
from .base import BaseTool


class ListDirectoryTool(BaseTool):
    """List immediate workspace directory entries without recursive traversal."""

    name = "list_directory"

    def schema(self) -> dict[str, Any]:
        """Describe the workspace directory path argument.

        Args:
            None.

        Returns:
            One OpenAI-compatible function tool definition.
        """
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

    def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        """Check the directory path and return sorted names up to the entry cap.

        Args:
            arguments: Tool argument mapping; validated before execution.
            context: Task policy, approval callback and optional skill catalog.

        Returns:
            JSON-serializable tool result; registry wrappers convert errors.
        """
        path = context.policy.path(arguments["path"])
        if not path.is_dir():
            raise FileNotFoundError(arguments["path"])
        entries = sorted(replace_unpaired_surrogates(entry.name)[0] for entry in path.iterdir())
        return {"ok": True, "entries": entries[:DIRECTORY_ENTRY_LIMIT]}
