from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .skills import SkillCatalog

DEFAULT_MAX_BYTES = 32_768
DIRECTORY_ENTRY_LIMIT = 200
PROCESS_TIMEOUT_SECONDS = 30


@dataclass
class Policy:
    """Shared workspace boundary, opt-in capabilities and per-result byte budget."""

    workspace: Path
    allow_write: bool = False
    allow_command: bool = False
    max_bytes: int = DEFAULT_MAX_BYTES

    def path(self, value: str) -> Path:
        """Resolve a workspace path, rejecting detected symlinks and escapes.

        Application checks are not race-free isolation and do not comprehensively
        detect every platform-specific reparse point.

        Args:
            value: Absolute or workspace-relative path requested by the tool.

        Returns:
            Resolved absolute path within the configured workspace.
        """
        raw = Path(value)
        candidate_raw = raw if raw.is_absolute() else self.workspace / raw
        current = self.workspace
        try:
            relative_parts = candidate_raw.relative_to(self.workspace).parts
        except ValueError:
            relative_parts = ()
        for part in relative_parts:
            current = current / part
            if current.is_symlink():
                raise PermissionError("symlink or reparse path is not allowed")
        candidate = candidate_raw.resolve()
        root = self.workspace.resolve()
        if candidate != root and root not in candidate.parents:
            raise PermissionError("path is outside the workspace")
        return candidate


@dataclass(frozen=True)
class ToolExecutionContext:
    """Provide the user-configured task environment and execution dependencies.

    Attributes:
        policy: User-configured workspace and capabilities used for safety checks.
        confirm: Callback requesting user approval for gated operations.
        catalog: Skills available to this task, or None when unavailable.

    This container does not validate arguments or authorize operations itself.
    BaseTool.validate handles argument structure/types; policy and concrete tools
    enforce execution permissions. Frozen fields do not freeze referenced objects.
    """

    policy: Policy
    confirm: Callable[[str], bool]
    catalog: SkillCatalog | None = None


def bounded(text: str, limit: int) -> dict[str, Any]:
    """Cap returned UTF-8 bytes and mark truncation; input is already in memory.

    Args:
        text: Already captured text to limit for the returned result.
        limit: Maximum UTF-8 byte count retained in the response.

    Returns:
        Text and a flag indicating whether UTF-8 output was truncated.
    """
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return {"text": text, "truncated": False}
    return {"text": raw[:limit].decode("utf-8", "ignore"), "truncated": True}
