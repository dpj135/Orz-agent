"""Keep the advertised local checks in sync with development dependencies."""

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_commit_checks_are_cross_platform_and_offline():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = config["repos"][0]["hooks"]
    assert config["repos"][0]["repo"] == "local"
    assert all(hook["language"] == "system" for hook in hooks)
    assert all(hook["entry"].startswith("python -m ") for hook in hooks)
    tests = next(hook for hook in hooks if hook["id"] == "pytest")
    assert tests["always_run"] is True
    assert tests["pass_filenames"] is False
    assert {"ruff-check", "ruff-format", "check-yaml", "check-toml", "check-json"} <= {
        hook["id"] for hook in hooks
    }


def test_check_tools_are_pinned_in_dev_dependencies():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = config["project"]["optional-dependencies"]["dev"]
    for tool in ("pre-commit", "pre-commit-hooks", "ruff"):
        assert any(dependency.startswith(tool + "==") for dependency in dev)


def test_line_length_is_checked_by_ruff():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["ruff"]["line-length"] == 100
    assert "E501" in config["tool"]["ruff"]["lint"]["select"]
