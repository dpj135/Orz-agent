from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import Any

from ..file_search import checked_path
from ..tool_execution_context import ToolExecutionContext
from .base import BaseTool


def read_edit_target(context: ToolExecutionContext, value: str) -> tuple[Path, bytes]:
    """Read a regular workspace file without crossing linked path components.

    Args:
        context: Task workspace and byte budget.
        value: Requested workspace file path.

    Returns:
        Resolved path and bounded original bytes.
    """
    raw = Path(value)
    checked_path(raw if raw.is_absolute() else context.policy.workspace.absolute() / raw)
    path = context.policy.path(value)
    current = context.policy.workspace.resolve()
    for part in path.relative_to(current).parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PermissionError("linked/reparse paths are not allowed")
    if not path.is_file():
        raise ValueError("edit target must be a regular file")
    with path.open("rb") as handle:
        data = handle.read(context.policy.max_bytes + 1)
    if len(data) > context.policy.max_bytes:
        raise ValueError("edit target exceeds byte limit")
    return path, data


class EditFileTool(BaseTool):
    """Replace one exact text occurrence after write permission and user approval."""

    name = "edit_file"

    def schema(self) -> dict[str, Any]:
        """Describe a unique literal replacement and optional stale-content guard.

        Args:
            None.

        Returns:
            One OpenAI function definition.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Replace exactly one literal match in a UTF-8 workspace file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                        "expected_sha256": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                    "additionalProperties": False,
                },
            },
        }

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Reject empty search text and malformed optional digests.

        Args:
            arguments: Raw model arguments.

        Returns:
            Validated independent argument mapping.
        """
        args = super().validate(arguments)
        if not args["old_text"]:
            raise ValueError("old_text must not be empty")
        digest = args.get("expected_sha256")
        if digest is not None and (
            len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("expected_sha256 must be a lowercase SHA-256 digest")
        return args

    def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        """Check unique match, obtain approval and reject changes made during approval.

        Args:
            arguments: Validated replacement and optional digest.
            context: Task policy and approval callback.

        Returns:
            Success with replacement count and resulting SHA-256; errors are raised.
        """
        policy = context.policy
        if not policy.allow_write:
            raise PermissionError("write denied")
        path, original_bytes = read_edit_target(context, arguments["path"])
        try:
            original = original_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("edit target must be UTF-8 text") from exc
        digest = hashlib.sha256(original_bytes).hexdigest()
        if arguments.get("expected_sha256", digest) != digest:
            raise ValueError("file content changed: digest mismatch")
        if original.count(arguments["old_text"]) != 1:
            raise ValueError("old_text must match exactly once")
        updated = original.replace(arguments["old_text"], arguments["new_text"], 1)
        if len(updated.encode("utf-8")) > policy.max_bytes:
            raise ValueError("updated file exceeds byte limit")
        preview = (
            f"edit {arguments['path']} (one replacement, "
            f"old={arguments['old_text']!r}, new={arguments['new_text']!r})? [y/N] "
        )
        if not context.confirm(preview):
            raise PermissionError("edit denied")
        if policy.path(arguments["path"]) != path:
            raise ValueError("file path changed during approval")
        _, current_bytes = read_edit_target(context, arguments["path"])
        if current_bytes != original_bytes:
            raise ValueError("file changed during approval")
        path.write_bytes(updated.encode("utf-8"))
        return {
            "ok": True,
            "replacements": 1,
            "sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
        }
