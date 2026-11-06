from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from ..file_search import checked_path
from ..pytest_process import (
    MAX_PYTEST_TIMEOUT_SECONDS,
    PYTEST_TIMEOUT_SECONDS,
    run_pytest_process,
)
from ..tool_execution_context import ToolExecutionContext
from .base import BaseTool

MAX_TEST_PATHS = 20


class RunPytestTool(BaseTool):
    """Execute trusted workspace tests with explicit command permission and approval."""

    name = "run_pytest"

    def schema(self) -> dict[str, Any]:
        """Describe test paths and a bounded timeout, never arbitrary pytest flags.

        Args:
            None.

        Returns:
            One OpenAI-compatible tool definition.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Run approved pytest paths; tests execute code, not a safe read operation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "timeout": {"type": "integer", "minimum": 1},
                    },
                    "required": ["paths"],
                    "additionalProperties": False,
                },
            },
        }

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Reject options, node selectors and excessive paths/timeouts.

        Args:
            arguments: Raw model arguments.

        Returns:
            Validated path list and optional timeout.
        """
        args = super().validate(arguments)
        if not args["paths"] or len(args["paths"]) > MAX_TEST_PATHS:
            raise ValueError("provide 1 to 20 test paths")
        if any(not path or path.startswith("-") or "::" in path for path in args["paths"]):
            raise ValueError("only filesystem test paths are accepted")
        if args.get("timeout", PYTEST_TIMEOUT_SECONDS) > MAX_PYTEST_TIMEOUT_SECONDS:
            raise ValueError("pytest timeout exceeds maximum")
        return args

    def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        """Authorize code execution and run pytest with fixed command arguments.

        Args:
            arguments: Validated test paths and optional timeout.
            context: Task permission policy and user approval callback.

        Returns:
            Exit code, bounded output/summary, timeout and cleanup status.
        """
        if not context.policy.allow_command:
            raise PermissionError("command denied")
        paths = []
        for value in arguments["paths"]:
            raw = Path(value)
            checked_path(raw if raw.is_absolute() else context.policy.workspace.absolute() / raw)
            paths.append(context.policy.path(value))
        if any(not path.exists() for path in paths):
            raise FileNotFoundError("test path does not exist")
        timeout = arguments.get("timeout", PYTEST_TIMEOUT_SECONDS)
        argv = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            os.devnull,
            "-p",
            "no:cacheprovider",
            "--",
            *(str(path) for path in paths),
        ]
        if not context.confirm(f"Execute test code: {argv!r}, timeout={timeout}s? [y/N] "):
            raise PermissionError("pytest denied")
        result = run_pytest_process(argv, context, timeout)
        # Retained output can be a prefix when truncated; never invent pass counts.
        result["summary"] = (
            "Output truncated; inspect exit code and output."
            if result["truncated"]
            else "\n".join(result["output"].strip().splitlines()[-1:])
        )
        return result
