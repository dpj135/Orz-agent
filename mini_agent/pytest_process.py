"""Bounded capture for approved pytest execution; not an OS sandbox."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from typing import Any

from .tool_execution_context import ToolExecutionContext

PYTEST_TIMEOUT_SECONDS = 60
MAX_PYTEST_TIMEOUT_SECONDS = 120
CAPTURE_CHUNK_BYTES = 4096
TREE_KILL_TIMEOUT_SECONDS = 5
READER_JOIN_TIMEOUT_SECONDS = 2


def run_pytest_process(
    argv: list[str], context: ToolExecutionContext, timeout: int
) -> dict[str, Any]:
    """Run an approved command with drained pipes and bounded retained output.

    Args:
        argv: Fixed interpreter/pytest command and validated test paths.
        context: Workspace and output budget.
        timeout: Maximum running time in seconds.

    Returns:
        Exit status, bounded combined output, truncation and timeout flags.
    """
    # Keep basic OS/locale paths, but never inherit API keys, pytest config flags,
    # PYTHONPATH, or automatic third-party pytest plugin loading.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
        "VIRTUAL_ENV",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    process = subprocess.Popen(
        argv,
        cwd=context.policy.workspace,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
    )
    captured = bytearray()
    truncated = False

    def drain() -> None:
        """Drain output without retaining more than the task byte budget.

        Args:
            None.

        Returns:
            None; updates local capture and truncation flag.
        """
        nonlocal truncated
        while chunk := process.stdout.read(CAPTURE_CHUNK_BYTES):
            remaining = max(0, context.policy.max_bytes - len(captured))
            captured.extend(chunk[:remaining])
            truncated |= len(chunk) > remaining

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # Attempt descendant cleanup even when the parent exited leaving open pipes.
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
            try:
                subprocess.run(
                    [
                        os.path.join(system_root, "System32", "taskkill.exe"),
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=TREE_KILL_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=READER_JOIN_TIMEOUT_SECONDS)
    decoded = bytes(captured).decode("utf-8", "replace").encode("utf-8")
    budget = max(0, context.policy.max_bytes)
    truncated |= len(decoded) > budget
    output = decoded[:budget].decode("utf-8", "ignore")
    return {
        "ok": not timed_out and process.returncode == 0,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "output": output,
        "truncated": truncated,
        "cleanup_incomplete": reader.is_alive(),
    }
