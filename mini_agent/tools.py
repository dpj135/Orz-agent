from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class Policy:
    workspace: Path
    allow_write: bool = False
    allow_command: bool = False
    max_bytes: int = 32_768

    def path(self, value: str) -> Path:
        raw = Path(value)
        if raw.is_absolute():
            candidate_raw = raw
        else:
            candidate_raw = self.workspace / raw
        # Reject reparse/symlink components before resolving. This is a conservative
        # application-level guard, not a replacement for an OS sandbox.
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


def _strict_args(args: dict[str, Any], required: set[str], optional: set[str] | None = None) -> None:
    optional = optional or set()
    if set(args) - required - optional or not required.issubset(args):
        raise ValueError("invalid tool arguments")


def _bounded(text: str, limit: int) -> dict[str, Any]:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return {"text": text, "truncated": False}
    return {"text": raw[:limit].decode("utf-8", "ignore"), "truncated": True}


def schemas() -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": "list_directory", "description": "List entries in a workspace directory.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file in the workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "max_bytes": {"type": "integer", "minimum": 1}}, "required": ["path"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "write_file", "description": "Write UTF-8 text in the workspace when permitted.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "run_command", "description": "Run an explicitly permitted executable without a shell.", "parameters": {"type": "object", "properties": {"program": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}, "required": ["program", "args"], "additionalProperties": False}}},
    ]


def execute(name: str, args: dict[str, Any], policy: Policy, confirm: Callable[[str], bool]) -> dict[str, Any]:
    try:
        if name == "list_directory":
            _strict_args(args, {"path"})
            path = policy.path(args["path"])
            if not path.is_dir(): raise FileNotFoundError(args["path"])
            entries = sorted(p.name for p in path.iterdir())[:200]
            return {"ok": True, "entries": entries}
        if name == "read_file":
            _strict_args(args, {"path"}, {"max_bytes"})
            path = policy.path(args["path"])
            if not path.is_file(): raise FileNotFoundError(args["path"])
            return {"ok": True, **_bounded(path.read_text(encoding="utf-8"), min(int(args.get("max_bytes", policy.max_bytes)), policy.max_bytes))}
        if name == "write_file":
            _strict_args(args, {"path", "content"})
            if not policy.allow_write or not confirm(f"write {args['path']}? [y/N] "): raise PermissionError("write denied")
            path = policy.path(args["path"])
            content = args["content"]
            if len(content.encode()) > policy.max_bytes: raise ValueError("content exceeds limit")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"ok": True, "path": str(path)}
        if name == "run_command":
            _strict_args(args, {"program", "args"})
            if not policy.allow_command or not confirm(f"run {args['program']}? [y/N] "): raise PermissionError("command denied")
            if not isinstance(args["args"], list) or not all(isinstance(item, str) for item in args["args"]): raise ValueError("args must be a string array")
            if os.path.basename(args["program"]) not in {"python", "python.exe", "git", "git.exe"}: raise PermissionError("program is not allowlisted")
            result = subprocess.run([args["program"], *args["args"]], cwd=policy.workspace, shell=False, capture_output=True, text=True, timeout=30)
            return {"ok": result.returncode == 0, "returncode": result.returncode, "stdout": _bounded(result.stdout, policy.max_bytes), "stderr": _bounded(result.stderr, policy.max_bytes)}
        raise ValueError("unknown tool")
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
