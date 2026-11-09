"""Process-level tests: a real CLI talks HTTP only to our loopback server."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest

from .conftest import (
    MockLLM,
    Reply,
    completion_fixture,
    decode_request,
    tool_response,
    validate_request,
)

CLIRunner = Callable[..., CompletedProcess[str]]
ServerFactory = Callable[[list[Reply]], MockLLM]


def assert_clean_exit(result: CompletedProcess[str], code: int = 0) -> None:
    assert result.returncode == code, (result.stdout, result.stderr)
    assert "Traceback" not in result.stdout + result.stderr


def tool_result(server: MockLLM) -> dict[str, Any]:
    assert len(server.requests) == 2
    results = [message for message in server.requests[1]["messages"] if message["role"] == "tool"]
    assert len(results) == 1
    assert results[0]["tool_call_id"] == "synthetic-call-1"
    return json.loads(results[0]["content"])


def test_one_shot(mock_llm: ServerFactory, cli: CLIRunner) -> None:
    server = mock_llm([Reply(completion_fixture())])
    result = cli(server)
    assert_clean_exit(result)
    assert "Synthetic E2E answer." in result.stdout
    assert len(server.requests) == 1
    assert server.requests[0]["messages"][-1] == {
        "role": "user",
        "content": "Synthetic test prompt",
    }


def test_repl_two_turns(mock_llm: ServerFactory, cli: CLIRunner) -> None:
    server = mock_llm([Reply(completion_fixture()), Reply(completion_fixture())])
    result = cli(server, prompt=None, stdin="first turn\nsecond turn\n/exit\n")
    assert_clean_exit(result)
    assert "Enter a prompt" in result.stdout
    assert result.stdout.count("Synthetic E2E answer.") == 2
    assert [request["messages"][-1]["content"] for request in server.requests] == [
        "first turn",
        "second turn",
    ]


def test_repl_eof(mock_llm: ServerFactory, cli: CLIRunner) -> None:
    server = mock_llm([])
    assert_clean_exit(cli(server, prompt=None))
    assert server.requests == []


def test_write_approved(mock_llm: ServerFactory, cli: CLIRunner, tmp_path: Path) -> None:
    server = mock_llm([Reply(completion_fixture("tool_call")), Reply(completion_fixture())])
    result = cli(server, stdin="y\n", flags=("--allow-write",))
    assert_clean_exit(result)
    assert "write answer.txt? [y/N]" in result.stdout
    assert (tmp_path / "workspace" / "answer.txt").read_text(encoding="utf-8") == (
        "Written by synthetic E2E.\n"
    )
    assert tool_result(server)["ok"] is True


@pytest.mark.parametrize("stdin", ["n\n", ""], ids=["negative", "eof"])
def test_write_rejected(
    mock_llm: ServerFactory, cli: CLIRunner, tmp_path: Path, stdin: str
) -> None:
    server = mock_llm([Reply(completion_fixture("tool_call")), Reply(completion_fixture())])
    assert_clean_exit(cli(server, stdin=stdin, flags=("--allow-write",)))
    assert not (tmp_path / "workspace" / "answer.txt").exists()
    result = tool_result(server)
    assert result["ok"] is False
    assert "write denied" in result["error"]


def test_write_requires_opt_in(mock_llm: ServerFactory, cli: CLIRunner, tmp_path: Path) -> None:
    server = mock_llm([Reply(completion_fixture("tool_call")), Reply(completion_fixture())])
    assert_clean_exit(cli(server, stdin="y\n"))
    assert not (tmp_path / "workspace" / "answer.txt").exists()
    assert "write denied" in tool_result(server)["error"]


def test_path_escape(mock_llm: ServerFactory, cli: CLIRunner, tmp_path: Path) -> None:
    response = tool_response("write_file", {"path": "../escape.txt", "content": "must not exist"})
    server = mock_llm([Reply(response), Reply(completion_fixture())])
    assert_clean_exit(cli(server, stdin="y\n", flags=("--allow-write",)))
    assert not (tmp_path / "escape.txt").exists()
    result = tool_result(server)
    assert result["ok"] is False
    assert "workspace" in result["error"]


@pytest.mark.parametrize(
    ("program", "flags", "stdin", "error"),
    [
        ("not-allowlisted-e2e-program", ("--allow-command",), "y\n", "not allowlisted"),
        ("python", (), "y\n", "command denied"),
        ("python", ("--allow-command",), "n\n", "command denied"),
        ("python", ("--allow-command",), "", "command denied"),
    ],
    ids=["not-allowlisted", "no-opt-in", "approval-no", "approval-eof"],
)
def test_command_denied(
    mock_llm: ServerFactory,
    cli: CLIRunner,
    tmp_path: Path,
    program: str,
    flags: tuple[str, ...],
    stdin: str,
    error: str,
) -> None:
    response = tool_response(
        "run_command",
        {
            "program": program,
            "args": ["-c", "from pathlib import Path; Path('side-effect.txt').write_text('bad')"],
        },
    )
    server = mock_llm([Reply(response), Reply(completion_fixture())])
    assert_clean_exit(cli(server, stdin=stdin, flags=flags))
    result = tool_result(server)
    assert result["ok"] is False
    assert error in result["error"]
    assert not (tmp_path / "workspace" / "side-effect.txt").exists()


def test_max_steps(mock_llm: ServerFactory, cli: CLIRunner, tmp_path: Path) -> None:
    server = mock_llm([Reply(completion_fixture("tool_call"))])
    result = cli(server, flags=("--max-steps", "1"))
    assert_clean_exit(result, 1)
    assert "work loop exceeded max_steps=1" in result.stdout
    assert len(server.requests) == 1
    assert not (tmp_path / "workspace" / "answer.txt").exists()


@pytest.mark.parametrize("arguments", ["{not-json", "[]", "null"])
def test_invalid_tool_arguments(
    mock_llm: ServerFactory, cli: CLIRunner, tmp_path: Path, arguments: str
) -> None:
    response = completion_fixture("tool_call")
    response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = arguments
    server = mock_llm([Reply(response), Reply(completion_fixture())])
    assert_clean_exit(cli(server, stdin="y\n", flags=("--allow-write",)))
    result = tool_result(server)
    assert result["ok"] is False
    assert "invalid tool arguments" in result["error"]
    assert not (tmp_path / "workspace" / "answer.txt").exists()


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_http_errors(mock_llm: ServerFactory, cli: CLIRunner, status: int) -> None:
    server = mock_llm([Reply({"error": {"message": "synthetic provider failure"}}, status)])
    result = cli(server)
    assert_clean_exit(result, 1)
    assert f"model API error {status}" in result.stdout
    assert "synthetic provider failure" in result.stdout
    assert len(server.requests) == 1


def test_missing_api_key(cli: CLIRunner) -> None:
    result = cli(None)
    assert_clean_exit(result, 2)
    assert "Missing API key" in result.stdout


def test_surrogate_stdin_regression(mock_llm: ServerFactory, cli: CLIRunner) -> None:
    server = mock_llm([Reply(completion_fixture())])
    # UTF-8 surrogatepass reproduces the invalid decoded input cross-platform.
    # The actual CLI REPL input and sanitize_user_input run in the child process.
    raw_input = "before\ud800after\n/exit\n".encode("utf-8", "surrogatepass")
    result = cli(server, prompt=None, stdin=raw_input)
    assert_clean_exit(result)
    assert "Synthetic E2E answer." in result.stdout
    assert len(server.requests) == 1
    assert server.requests[0]["messages"][-1]["content"] == "before\ufffdafter"
    assert "replaced 1 unpaired surrogate" in result.stderr


@pytest.mark.parametrize("raw", [b"not-json", b'{"model":', b"\xff"])
def test_protocol_decoder_rejects_invalid_json(raw: bytes) -> None:
    with pytest.raises((ValueError, UnicodeDecodeError)):
        decode_request(raw)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model", 123, "model"),
        ("messages", [], "messages"),
        ("messages", [{"role": "unsupported", "content": "x"}], "role"),
        ("messages", [{"role": "user", "content": 123}], "content"),
        ("messages", [{"role": "tool", "content": "{}", "tool_call_id": "missing"}], "preceding"),
        ("tools", {}, "tools"),
        ("tools", [{"type": "invalid"}], "type=function"),
        ("tool_choice", "invalid", "tool_choice"),
    ],
)
def test_protocol_validator_rejects_invalid_structure(field: str, value: Any, message: str) -> None:
    body = {
        "model": "synthetic-model",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "synthetic_tool",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            }
        ],
        "tool_choice": "auto",
    }
    body[field] = value
    with pytest.raises(ValueError, match=message):
        validate_request(body)


def test_protocol_validator_rejects_escaped_surrogate() -> None:
    # json.loads alone accepts escaped surrogates; the independent check must not.
    with pytest.raises(ValueError, match="surrogate"):
        validate_request(json.loads('{"messages": [{"content": "\\ud800"}]}'))
