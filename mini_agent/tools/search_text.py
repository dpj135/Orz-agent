from __future__ import annotations

import codecs
from typing import Any

from .. import file_search
from ..text_safety import json_size, replace_unpaired_surrogates
from ..tool_execution_context import ToolExecutionContext
from .base import BaseTool


class SearchTextTool(BaseTool):
    """Search bounded UTF-8 file prefixes for a case-sensitive literal substring."""

    name = "search_text"

    def schema(self) -> dict[str, Any]:
        """Describe required directory and literal pattern arguments.

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
                    "Search UTF-8 files below path for a case-sensitive literal pattern, "
                    "not regex. "
                    "Return workspace-relative paths, one-based line numbers, and matching lines. "
                    "Binary and invalid UTF-8 files are skipped; reads and results are capped."
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
        """Search checked regular files with bounded reads before decoding.

        Args:
            arguments: Validated directory, literal pattern, and optional result limit.
            context: Task policy defining the workspace and response byte cap.

        Returns:
            Matching lines and a flag for omitted results, bytes, or unsafe paths.
        """
        pattern = arguments["pattern"]
        if not pattern or "\n" in pattern or "\r" in pattern:
            raise ValueError("pattern must be a nonempty single-line literal")
        directory = file_search.search_root(arguments["path"], context)
        workspace = context.policy.workspace.absolute()
        traversal = file_search.SearchTraversal(directory)
        limit = min(arguments.get("max_results", file_search.MAX_RESULTS), file_search.MAX_RESULTS)
        matches: list[dict[str, Any]] = []
        budget = max(0, context.policy.max_bytes)
        used = 0
        truncated = False
        for path in traversal.files():
            try:
                file_search.checked_path(path)
                with path.open("rb") as stream:
                    raw = stream.read(file_search.MAX_FILE_BYTES + 1)
                oversized = len(raw) > file_search.MAX_FILE_BYTES
                truncated = truncated or oversized
                raw = raw[: file_search.MAX_FILE_BYTES]
                if b"\x00" in raw:
                    continue
                # Ignore only an incomplete final UTF-8 sequence caused by the read cap.
                text = raw.decode("utf-8", "strict")
            except UnicodeDecodeError:
                if not oversized:
                    continue
                try:
                    text = codecs.getincrementaldecoder("utf-8")().decode(raw, final=False)
                except UnicodeDecodeError:
                    continue
            except OSError:
                truncated = True
                continue
            relative, _ = replace_unpaired_surrogates(path.relative_to(workspace).as_posix())
            for number, line in enumerate(text.splitlines(), 1):
                if pattern not in line:
                    continue
                match = {"path": relative, "line": number, "text": line}
                size = json_size(match) + 2
                if len(matches) >= limit or used + size > budget:
                    return {"ok": True, "matches": matches, "truncated": True}
                matches.append(match)
                used += size
        return {"ok": True, "matches": matches, "truncated": truncated or traversal.truncated}
