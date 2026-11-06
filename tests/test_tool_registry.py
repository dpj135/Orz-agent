import ast
import subprocess
from pathlib import Path

import pytest

from mini_agent.tool_execution_context import Policy, ToolExecutionContext
from mini_agent.tool_registry import ToolRegistry
from mini_agent.tools.base import BaseTool
from mini_agent.tools.read_file import ReadFileTool


def context(root, **kwargs):
    return ToolExecutionContext(Policy(root, **kwargs), lambda _: True)


def test_discovery_and_schema_copy():
    registry = ToolRegistry.discover()
    names = [schema["function"]["name"] for schema in registry.schemas()]
    assert names == [
        "edit_file",
        "glob_files",
        "list_directory",
        "read_file",
        "run_command",
        "run_pytest",
        "search_text",
        "skill",
        "write_file",
    ]
    schemas = registry.schemas()
    schemas[0]["function"]["name"] = "changed"
    assert registry.schemas()[0]["function"]["name"] == "edit_file"


def test_duplicate_name_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry([ReadFileTool(), ReadFileTool()])


def test_schema_mismatch_rejected():
    class BadTool(ReadFileTool):
        def schema(self):
            schema = super().schema()
            schema["function"]["name"] = "wrong"
            return schema

    with pytest.raises(ValueError, match="schema"):
        ToolRegistry([BadTool()])


def test_base_is_abstract():
    with pytest.raises(TypeError):
        BaseTool()


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": 1},
        {"path": "x", "extra": True},
        {"path": "x", "max_bytes": 0},
        {"path": "x", "max_bytes": True},
        {"path": "x", "max_bytes": "10"},
        [],
    ],
)
def test_invalid_arguments_rejected(tmp_path, arguments):
    result = ToolRegistry.discover().execute("read_file", arguments, context(tmp_path))
    assert not result["ok"]
    assert "ValueError" in result["error"]


def test_unknown_and_handler_failure(tmp_path):
    registry = ToolRegistry.discover()
    assert not registry.execute("unknown", {}, context(tmp_path))["ok"]
    result = registry.execute("read_file", {"path": "missing"}, context(tmp_path))
    assert "FileNotFoundError" in result["error"]


def test_files_preserve_behavior(tmp_path):
    registry = ToolRegistry.discover()
    ctx = context(tmp_path, allow_write=True)
    assert registry.execute("write_file", {"path": "note", "content": "hello"}, ctx)["ok"]
    assert registry.execute("read_file", {"path": "note", "max_bytes": 2}, ctx) == {
        "ok": True,
        "text": "he",
        "truncated": True,
    }
    assert registry.execute("list_directory", {"path": "."}, ctx)["entries"] == ["note"]
    assert not registry.execute("read_file", {"path": "../outside"}, ctx)["ok"]


def test_process_permission_and_invocation(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = ToolRegistry.discover()
    args = {"program": "git", "args": ["--version"]}
    assert not registry.execute("run_command", args, context(tmp_path))["ok"]
    assert not calls
    assert registry.execute("run_command", args, context(tmp_path, allow_command=True))["ok"]
    assert calls[0][0] == ["git", "--version"]
    assert calls[0][1]["shell"] is False
    assert not registry.execute(
        "run_command", {"program": "bad", "args": []}, context(tmp_path, allow_command=True)
    )["ok"]
    assert len(calls) == 1


def test_workspace_python_is_never_imported(tmp_path, monkeypatch):
    (tmp_path / "read_file.py").write_text("raise RuntimeError('untrusted')")
    monkeypatch.chdir(tmp_path)
    assert len(ToolRegistry.discover().schemas()) == 9


def test_concrete_tools_do_not_import_each_other():
    directory = Path(__file__).resolve().parents[1] / "mini_agent" / "tools"
    concrete = {path.stem for path in directory.glob("*.py")} - {"__init__", "base"}
    for name in concrete:
        tree = ast.parse((directory / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[-1] not in concrete
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[-1] not in concrete for alias in node.names)
