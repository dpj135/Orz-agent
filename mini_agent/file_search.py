"""Bounded workspace search helpers; application checks do not remove filesystem races."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .tool_execution_context import ToolExecutionContext

MAX_TRAVERSAL_ENTRIES = 10_000
MAX_FILE_BYTES = 65_536
MAX_RESULTS = 100
EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".test-deps",
        ".test-work",
        ".pytest-work",
    }
)


def excluded(name: str) -> bool:
    """Identify dependency, cache, and pip temporary directory names.

    Args:
        name: A single path component.

    Returns:
        Whether traversal must omit the component.
    """
    lowered = name.casefold()
    return lowered in EXCLUDED_NAMES or lowered.startswith(("pip-", "pip_", "~ip"))


def checked_path(path: Path) -> Path:
    """Reject symbolic links and Windows reparse points in existing ancestors.

    Args:
        path: Absolute lexical path to inspect without resolving links.

    Returns:
        The unchanged path if every existing component passes inspection.
    """
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PermissionError("symlink or reparse path is not allowed")
    return path


def search_root(value: str, context: ToolExecutionContext) -> Path:
    """Validate a requested directory inside a non-linked workspace.

    Args:
        value: Absolute or workspace-relative directory path.
        context: Task policy containing the workspace boundary.

    Returns:
        Validated search directory.
    """
    root = Path(os.path.abspath(context.policy.workspace))
    raw = Path(value)
    if ".." in raw.parts:
        raise PermissionError("parent traversal is not allowed")
    candidate = raw if raw.is_absolute() else root / raw
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError("path is outside the workspace") from exc
    if any(part == ".." or excluded(part) for part in relative.parts):
        raise PermissionError("invalid search path")
    checked_path(root)
    checked_path(candidate)
    if not candidate.is_dir():
        raise NotADirectoryError(value)
    return candidate


@dataclass
class SearchTraversal:
    """Track a bounded deterministic walk, including skipped unsafe paths."""

    directory: Path
    truncated: bool = False
    visited: int = 0

    def files(self) -> Iterator[Path]:
        """Yield regular files in sorted depth-first order without following links.

        Args:
            None.

        Returns:
            Iterator of checked files; flags remain on this traversal instance.
        """
        pending = [self.directory]
        while pending:
            path = pending.pop()
            try:
                checked_path(path)
                info = path.lstat()
                if stat.S_ISREG(info.st_mode):
                    yield path
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    continue
                entries = []
                with os.scandir(path) as iterator:
                    for entry in iterator:
                        if self.visited >= MAX_TRAVERSAL_ENTRIES:
                            self.truncated = True
                            return
                        self.visited += 1
                        entries.append(Path(entry.path))
                pending.extend(
                    entry
                    for entry in sorted(entries, key=lambda item: item.name, reverse=True)
                    if not excluded(entry.name)
                )
            except (OSError, ValueError, PermissionError):
                self.truncated = True
