from __future__ import annotations

import json
from pathlib import PurePath
from typing import Any

from .. import file_search
from ..tool_execution_context import ToolExecutionContext
from .base import BaseTool


class GlobFilesTool(BaseTool):
    """Find regular workspace files using pathlib.PurePath.match semantics."""

    name = "glob_files"

    def schema(self) -> dict[str, Any]:
        """Describe required directory and glob pattern arguments.

        Args:
            None.

        Returns:
            OpenAI-compatible tool definition.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Find files below path using PurePath.match on relative paths. "
                    "Relative patterns match from the right; ** is not a recursive wildcard. "
                    "Matching follows platform case rules. Results are workspace-relative."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "pattern": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path", "pattern"],
                    "additionalProperties": False,
                },
            },
        }

    def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        """Return bounded deterministic glob matches from the checked directory.

        Args:
            arguments: Validated directory, pattern, and optional result limit.
            context: Task policy defining the workspace and response byte cap.

        Returns:
            Relative file paths and a flag for omitted results or incomplete traversal.
        """
        pattern = arguments["pattern"]
        if not pattern or PurePath(pattern).is_absolute() or ".." in PurePath(pattern).parts:
            raise ValueError("pattern must be a nonempty relative glob")
        directory = file_search.search_root(arguments["path"], context)
        workspace = context.policy.workspace.absolute()
        traversal = file_search.SearchTraversal(directory)
        limit = min(arguments.get("max_results", file_search.MAX_RESULTS), file_search.MAX_RESULTS)
        paths: list[str] = []
        budget = max(0, context.policy.max_bytes)
        used = 0
        truncated = False
        for path in traversal.files():
            if not path.relative_to(directory).match(pattern):
                continue
            relative = path.relative_to(workspace).as_posix()
            size = len(json.dumps(relative, ensure_ascii=False).encode("utf-8")) + 2
            if len(paths) >= limit or used + size > budget:
                truncated = True
                break
            paths.append(relative)
            used += size
        return {"ok": True, "paths": paths, "truncated": truncated or traversal.truncated}
