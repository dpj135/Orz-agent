from __future__ import annotations

from typing import Any

from ..tool_context import ToolContext, bounded
from .base import BaseTool


class ReadFileTool(BaseTool):
    name = "read_file"

    def schema(self) -> dict[str, Any]:
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

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = context.policy.path(arguments["path"])
        if not path.is_file():
            raise FileNotFoundError(arguments["path"])
        limit = min(arguments.get("max_bytes", context.policy.max_bytes), context.policy.max_bytes)
        return {"ok": True, **bounded(path.read_text(encoding="utf-8"), limit)}
