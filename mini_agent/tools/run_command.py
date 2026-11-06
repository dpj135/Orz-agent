from __future__ import annotations

import os
import subprocess
from typing import Any

from ..tool_context import PROCESS_TIMEOUT_SECONDS, ToolContext, bounded
from .base import BaseTool

ALLOWED_PROGRAMS = frozenset({"python", "python.exe", "git", "git.exe"})


class RunCommandTool(BaseTool):
    name = "run_command"

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Run an explicitly permitted executable without a shell.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "program": {"type": "string"},
                        "args": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["program", "args"],
                    "additionalProperties": False,
                },
            },
        }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if not context.policy.allow_command or not context.confirm(
            f"run {arguments['program']}? [y/N] "
        ):
            raise PermissionError("command denied")
        if os.path.basename(arguments["program"]) not in ALLOWED_PROGRAMS:
            raise PermissionError("program is not allowlisted")
        result = subprocess.run(
            [arguments["program"], *arguments["args"]],
            cwd=context.policy.workspace,
            shell=False,
            capture_output=True,
            text=True,
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": bounded(result.stdout, context.policy.max_bytes),
            "stderr": bounded(result.stderr, context.policy.max_bytes),
        }
