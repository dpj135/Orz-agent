import hashlib
import sys

import pytest

from mini_agent.pytest_process import run_pytest_process
from mini_agent.tool_execution_context import Policy, ToolExecutionContext
from mini_agent.tool_registry import ToolRegistry


def execute(root, name, args, approve=True, **permissions):
    return ToolRegistry.discover().execute(
        name, args, ToolExecutionContext(Policy(root, **permissions), lambda _: approve)
    )


def test_edit_unique_and_digest(tmp_path):
    path = tmp_path / "note"
    path.write_bytes(b"hello\r\nworld")
    result = execute(
        tmp_path,
        "edit_file",
        {
            "path": "note",
            "old_text": "world",
            "new_text": "agent",
            "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        allow_write=True,
    )
    assert result["ok"]
    assert path.read_bytes() == b"hello\r\nagent"


@pytest.mark.parametrize("old", ["missing", "", "a"])
def test_edit_match_errors_do_not_write(tmp_path, old):
    path = tmp_path / "note"
    path.write_text("a a", encoding="utf-8")
    result = execute(
        tmp_path,
        "edit_file",
        {
            "path": "note",
            "old_text": old,
            "new_text": "x",
        },
        allow_write=True,
    )
    assert not result["ok"]
    assert path.read_text() == "a a"


def test_edit_permission_and_refusal(tmp_path):
    path = tmp_path / "note"
    path.write_text("before", encoding="utf-8")
    args = {"path": "note", "old_text": "before", "new_text": "after"}
    assert not execute(tmp_path, "edit_file", args)["ok"]
    assert not execute(tmp_path, "edit_file", args, approve=False, allow_write=True)["ok"]
    assert path.read_text() == "before"


def test_edit_concurrent_change(tmp_path):
    path = tmp_path / "note"
    path.write_text("before", encoding="utf-8")

    def confirm(_):
        path.write_text("external", encoding="utf-8")
        return True

    ctx = ToolExecutionContext(Policy(tmp_path, allow_write=True), confirm)
    result = ToolRegistry.discover().execute(
        "edit_file",
        {
            "path": "note",
            "old_text": "before",
            "new_text": "after",
        },
        ctx,
    )
    assert not result["ok"]
    assert path.read_text() == "external"


@pytest.mark.parametrize(
    "args",
    [
        {"paths": []},
        {"paths": ["--help"]},
        {"paths": ["x::node"]},
        {"paths": ["."], "timeout": 121},
        {"paths": ["."], "timeout": True},
    ],
)
def test_pytest_invalid_args(tmp_path, args):
    assert not execute(tmp_path, "run_pytest", args, allow_command=True)["ok"]


def test_pytest_permission_and_refusal(tmp_path):
    assert not execute(tmp_path, "run_pytest", {"paths": ["."]})["ok"]
    assert not execute(tmp_path, "run_pytest", {"paths": ["."]}, approve=False, allow_command=True)[
        "ok"
    ]


def test_pytest_real_smoke(tmp_path):
    (tmp_path / "test_example.py").write_text("def test_ok():\n    assert True\n")
    result = execute(tmp_path, "run_pytest", {"paths": ["test_example.py"]}, allow_command=True)
    assert result["ok"], result
    assert "1 passed" in result["output"]
    (tmp_path / "test_example.py").write_text("def test_bad():\n    assert False\n")
    result = execute(tmp_path, "run_pytest", {"paths": ["test_example.py"]}, allow_command=True)
    assert not result["ok"]
    assert result["returncode"] == 1


def test_capture_limit_timeout_and_secret_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "fake-eval-secret")
    ctx = ToolExecutionContext(Policy(tmp_path, max_bytes=40), lambda _: True)
    result = run_pytest_process(
        [sys.executable, "-c", "import os; print(os.getenv('AGENT_API_KEY')); print('x'*1000)"],
        ctx,
        10,
    )
    assert result["truncated"]
    assert "fake-eval-secret" not in result["output"]
    assert len(result["output"].encode()) <= 40
    result = run_pytest_process([sys.executable, "-c", "import time; time.sleep(10)"], ctx, 1)
    assert result["timed_out"]
    assert not result["ok"]
