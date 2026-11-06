from __future__ import annotations

from typing import Any

from ..tool_execution_context import ToolExecutionContext, bounded
from .base import BaseTool


class ReadFileTool(BaseTool):
    """Read workspace UTF-8 files with a policy-capped response."""

    name = "read_file"

    def schema(self) -> dict[str, Any]:
        """Describe the path and optional positive byte limit.

        Args:
            None.

        Returns:
            One OpenAI-compatible function tool definition.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Read a UTF-8 text file in the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_bytes": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }

    def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        """Check the workspace path, read text, and truncate the returned bytes.

        Args:
            arguments: Tool argument mapping; validated before execution.
            context: Task policy, approval callback and optional skill catalog.

        Returns:
            JSON-serializable tool result; registry wrappers convert errors.
        """
        path = context.policy.path(arguments["path"])
        if not path.is_file():
            raise FileNotFoundError(arguments["path"])
        limit = min(arguments.get("max_bytes", context.policy.max_bytes), context.policy.max_bytes)
        return {"ok": True, **bounded(path.read_text(encoding="utf-8"), limit)}
