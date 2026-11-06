from __future__ import annotations

from typing import Any

from ..tool_context import ToolExecutionContext
from .base import BaseTool


class WriteFileTool(BaseTool):
    """Create or overwrite workspace text only with permission and approval."""

    name = "write_file"

    def schema(self) -> dict[str, Any]:
        """Describe the target path and replacement UTF-8 content.

        Args:
            None.

        Returns:
            One OpenAI-compatible function tool definition.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Write UTF-8 text in the workspace when permitted.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
        }

    def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        """Require approval, check the path and byte budget, then write content.

        Args:
            arguments: Tool argument mapping; validated before execution.
            context: Task policy, approval callback and optional skill catalog.

        Returns:
            JSON-serializable tool result; registry wrappers convert errors.
        """
        if not context.policy.allow_write or not context.confirm(
            f"write {arguments['path']}? [y/N] "
        ):
            raise PermissionError("write denied")
        path = context.policy.path(arguments["path"])
        content = arguments["content"]
        if len(content.encode()) > context.policy.max_bytes:
            raise ValueError("content exceeds limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(path)}
