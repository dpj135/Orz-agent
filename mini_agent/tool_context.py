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
class ToolContext:
    """Per-task dependencies injected into tools instead of held on tool instances.

    Frozen fields do not make the referenced policy or skill catalog immutable.
    """

    policy: Policy
    confirm: Callable[[str], bool]
    catalog: SkillCatalog | None = None


def bounded(text: str, limit: int) -> dict[str, Any]:
    """Cap returned UTF-8 bytes and mark truncation; input is already in memory."""
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return {"text": text, "truncated": False}
    return {"text": raw[:limit].decode("utf-8", "ignore"), "truncated": True}
