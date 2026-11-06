"""Offline regression tests for bounded workspace search tools."""

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_agent import file_search
from mini_agent.tool_execution_context import Policy, ToolExecutionContext
from mini_agent.tool_registry import ToolRegistry
from mini_agent.tools.glob_files import GlobFilesTool
from mini_agent.tools.search_text import SearchTextTool


def run_tool(root, name, arguments, **policy):
    """Dispatch one search tool with a temporary workspace policy.

    Args:
        root: Workspace directory.
        name: Tool name.
        arguments: Untrusted argument mapping.
        policy: Optional policy overrides.

    Returns:
        Registry result mapping.
    """
    registry = ToolRegistry([GlobFilesTool(), SearchTextTool()])
    context = ToolExecutionContext(Policy(root, **policy), lambda _: False)
    return registry.execute(name, arguments, context)


@pytest.mark.parametrize("name", ["glob_files", "search_text"])
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": "."},
        {"pattern": "x"},
        {"path": 1, "pattern": "x"},
        {"path": ".", "pattern": 1},
        {"path": ".", "pattern": "x", "regex": "yes"},
        {"path": ".", "pattern": "x", "max_results": True},
        {"path": ".", "pattern": "x", "max_results": 0},
        {"path": ".", "pattern": ""},
    ],
)
def test_invalid_arguments(tmp_path, name, arguments):
    """Reject malformed search arguments before filesystem access.

    Args:
        tmp_path: Temporary workspace.
        name: Search tool name.
        arguments: Invalid mapping.

    Returns:
        None.
    """
    assert not run_tool(tmp_path, name, arguments)["ok"]


def test_glob_semantics_and_order(tmp_path):
    """Match PurePath semantics and sort paths independently of creation order.

    Args:
        tmp_path: Temporary workspace.

    Returns:
        None.
    """
    for name in ["z.py", "sub/b.py", "a.py", "sub/a.txt", "sub/deep/c.py"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("needle", encoding="utf-8")
    result = run_tool(tmp_path, "glob_files", {"path": ".", "pattern": "*.py"})
    assert result == {
        "ok": True,
        "paths": ["a.py", "sub/b.py", "sub/deep/c.py", "z.py"],
        "truncated": False,
    }
    result = run_tool(tmp_path, "glob_files", {"path": "sub", "pattern": "*.py"})
    assert result["paths"] == ["sub/b.py", "sub/deep/c.py"]
    for pattern in ["**/*.py", "sub/*.py", "*.txt"]:
        expected = [
            path
            for path in ["a.py", "sub/a.txt", "sub/b.py", "sub/deep/c.py", "z.py"]
            if Path(path).match(pattern)
        ]
        assert run_tool(tmp_path, "glob_files", {"path": ".", "pattern": pattern})["paths"] == (
            expected
        )


def test_literal_lines_binary_and_utf8(tmp_path):
    """Search literal metacharacters with one-based lines and skip non-text files.

    Args:
        tmp_path: Temporary workspace.

    Returns:
        None.
    """
    (tmp_path / "a.txt").write_text("no\n[a.*] café\n[a.*] twice [a.*]\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("A.*\n", encoding="utf-8")
    (tmp_path / "binary").write_bytes(b"[a.*]\x00")
    (tmp_path / "invalid").write_bytes(b"[a.*]\xff")
    result = run_tool(tmp_path, "search_text", {"path": ".", "pattern": "[a.*]"})
    assert result == {
        "ok": True,
        "matches": [
            {"path": "a.txt", "line": 2, "text": "[a.*] café"},
            {"path": "a.txt", "line": 3, "text": "[a.*] twice [a.*]"},
        ],
        "truncated": False,
    }


@pytest.mark.parametrize("name", ["glob_files", "search_text"])
def test_excluded_directories(tmp_path, name):
    """Omit dependencies, caches, and pip build directories recursively.

    Args:
        tmp_path: Temporary workspace.
        name: Search tool name.

    Returns:
        None.
    """
    for directory in [
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".test-deps",
        ".pytest_cache",
        "pip-build-xyz",
        "nested/.cache",
    ]:
        path = tmp_path / directory
        path.mkdir(parents=True)
        (path / "hidden").write_text("needle", encoding="utf-8")
    (tmp_path / "visible").write_text("needle", encoding="utf-8")
    pattern = "*" if name == "glob_files" else "needle"
    result = run_tool(tmp_path, name, {"path": ".", "pattern": pattern})
    assert not result["truncated"]
    assert result.get("paths", [item["path"] for item in result.get("matches", [])]) == ["visible"]
    assert not run_tool(tmp_path, name, {"path": ".git", "pattern": pattern})["ok"]


@pytest.mark.parametrize("name", ["glob_files", "search_text"])
def test_boundaries_and_result_limits(tmp_path, name):
    """Reject escapes and respect result and encoded payload byte caps.

    Args:
        tmp_path: Temporary workspace.
        name: Search tool name.

    Returns:
        None.
    """
    for index in range(3):
        (tmp_path / str(index)).write_text("needle", encoding="utf-8")
    pattern = "*" if name == "glob_files" else "needle"
    args = {"path": ".", "pattern": pattern, "max_results": 1}
    result = run_tool(tmp_path, name, args)
    assert result["truncated"]
    assert len(result.get("paths", result.get("matches"))) == 1
    result = run_tool(tmp_path, name, args, max_bytes=1)
    assert result["truncated"]
    assert result.get("paths", result.get("matches")) == []
    for path in ["../outside", str(tmp_path.parent), "missing", ".git/../0"]:
        assert not run_tool(tmp_path, name, {"path": path, "pattern": pattern})["ok"]


def test_traversal_and_file_caps(tmp_path, monkeypatch):
    """Mark entry limits and oversized file prefixes as incomplete.

    Args:
        tmp_path: Temporary workspace.
        monkeypatch: Temporary constant replacement fixture.

    Returns:
        None.
    """
    (tmp_path / "a").write_bytes(b"needle\n" + b"x" * 20 + b"hidden needle")
    monkeypatch.setattr(file_search, "MAX_FILE_BYTES", 10)
    result = run_tool(tmp_path, "search_text", {"path": ".", "pattern": "needle"})
    assert result["truncated"]
    assert result["matches"] == [{"path": "a", "line": 1, "text": "needle"}]
    (tmp_path / "b").write_text("needle", encoding="utf-8")
    monkeypatch.setattr(file_search, "MAX_TRAVERSAL_ENTRIES", 1)
    result = run_tool(tmp_path, "glob_files", {"path": ".", "pattern": "*"})
    assert result == {"ok": True, "paths": [], "truncated": True}


def test_symlink_rejected_and_not_read(tmp_path):
    """Reject explicit links and omit linked files during traversal.

    Args:
        tmp_path: Temporary workspace.

    Returns:
        None.
    """
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret").write_text("needle", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    result = run_tool(tmp_path, "search_text", {"path": "link", "pattern": "needle"})
    assert not result["ok"]
    result = run_tool(tmp_path, "glob_files", {"path": ".", "pattern": "*"})
    assert result["paths"] == ["target/secret"]
    assert result["truncated"]
    assert (target / "secret").read_text(encoding="utf-8") == "needle"


def test_reparse_attribute_rejected(tmp_path, monkeypatch):
    """Reject Windows reparse attributes even when symlink mode is not present.

    Args:
        tmp_path: Temporary workspace.
        monkeypatch: Temporary lstat replacement fixture.

    Returns:
        None.
    """
    original = Path.lstat

    def fake_lstat(path, *args, **kwargs):
        """Return a reparse attribute for the requested workspace only.

        Args:
            path: Inspected path.
            args: Positional arguments for the original method.
            kwargs: Keyword arguments for the original method.

        Returns:
            Real or synthetic stat information.
        """
        if path == tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    assert not run_tool(tmp_path, "glob_files", {"path": ".", "pattern": "*"})["ok"]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction coverage")
def test_windows_junction_rejected(tmp_path):
    """Reject an actual Windows directory junction without reading its target.

    Args:
        tmp_path: Temporary workspace.

    Returns:
        None.
    """
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret").write_text("needle", encoding="utf-8")
    junction = tmp_path / "junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        pytest.skip("junction creation unavailable on this host")
    try:
        assert not run_tool(
            tmp_path,
            "search_text",
            {
                "path": "junction",
                "pattern": "needle",
            },
        )["ok"]
        result = run_tool(tmp_path, "glob_files", {"path": ".", "pattern": "*"})
        assert result["paths"] == ["target/secret"]
    finally:
        junction.rmdir()
