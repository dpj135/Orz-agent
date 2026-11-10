"""Offline regressions for normalized model messages and interaction outcomes."""

import copy

import pytest

from mini_agent import cli
from mini_agent.loop import initial_messages, run_turn
from mini_agent.session import SessionStore
from mini_agent.tools import Policy
from tests.test_session_integration import FakeClient, final, read_history, tool_call


@pytest.mark.parametrize("shape", ["type", "id", "object", "two", "collision"])
def test_tool_normalization_persists_and_keeps_original_history(tmp_path, shape):
    response = tool_call()
    calls = response["choices"][0]["message"]["tool_calls"]
    if shape == "type":
        calls[0].pop("type")
    elif shape in {"id", "two", "collision"}:
        calls[0].pop("id")
        if shape == "two":
            calls.append(copy.deepcopy(calls[0]))
        elif shape == "collision":
            second = copy.deepcopy(calls[0])
            second["id"] = "call-0-0"
            calls.append(second)
    else:
        calls[0]["function"]["arguments"] = {"path": "note.txt"}
    original = copy.deepcopy(response)
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    root = tmp_path / "sessions"
    with SessionStore(root).create(tmp_path, initial_messages(Policy(tmp_path)), "normal") as s:
        client = FakeClient([response, final("done")])
        assert (
            run_turn("read", client, Policy(tmp_path), on_event=lambda _: None, session=s) == "done"
        )
        saved = s.messages
        normalized = saved[2]["tool_calls"]
        assert len({call["id"] for call in normalized}) == len(calls)
        assert all(call["type"] == "function" for call in normalized)
        assert all(isinstance(call["function"]["arguments"], str) for call in normalized)
        assert [m["tool_call_id"] for m in saved[3:-1]] == [c["id"] for c in normalized]
        assert client.requests[1][-1]["role"] == "tool"
    with SessionStore(root).resume("normal") as s:
        assert s.messages == saved
    events = read_history(root, "normal")
    assert next(e for e in events if e["event"] == "llm_response")["data"]["response"] == original
    assert response == original


@pytest.mark.parametrize("shape", ["duplicate", "type", "name", "arguments", "late_invalid"])
def test_invalid_call_batch_never_executes_first_tool(tmp_path, shape):
    response = tool_call()
    calls = response["choices"][0]["message"]["tool_calls"]
    if shape == "duplicate":
        calls.append(copy.deepcopy(calls[0]))
    elif shape == "type":
        calls[0]["type"] = "unknown"
    elif shape == "name":
        calls[0]["function"]["name"] = ""
    elif shape == "arguments":
        calls[0]["function"]["arguments"] = []
    else:
        calls.append({"id": "second", "type": "unknown"})
    root = tmp_path / "sessions"
    output = []
    with SessionStore(root).create(tmp_path, initial_messages(Policy(tmp_path)), "bad") as s:
        before = s.messages
        with pytest.raises(RuntimeError):
            run_turn(
                "read", FakeClient([response]), Policy(tmp_path), on_event=output.append, session=s
            )
        assert s.messages == before
    assert output == []
    events = read_history(root, "bad")
    assert not any(e["event"].startswith("tool_") for e in events)
    assert [e["data"]["status"] for e in events if e["event"] == "turn_end"] == ["failed"]


@pytest.mark.parametrize(
    "message,expected",
    [
        ({"role": "assistant", "content": None, "refusal": "Cannot help."}, "Cannot help."),
        ({"role": "assistant", "content": None}, ""),
        (
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}],
            },
            "onetwo",
        ),
    ],
)
def test_refusal_empty_and_parts_remain_resumable(tmp_path, message, expected):
    root = tmp_path / "sessions"
    output = []
    with SessionStore(root).create(tmp_path, initial_messages(Policy(tmp_path)), "text") as s:
        result = run_turn(
            "hello",
            FakeClient([{"choices": [{"message": message}]}]),
            Policy(tmp_path),
            on_event=output.append,
            session=s,
        )
        assert result == expected and isinstance(result, str)
        assert output == [expected]
        assert s.messages[-1] == message
    with SessionStore(root).resume("text") as s:
        assert s.messages[-1] == message


def test_repl_refusal_does_not_terminate_session(tmp_path, monkeypatch, capsys):
    refusal = {"role": "assistant", "content": None, "refusal": "Cannot help."}
    client = FakeClient([{"choices": [{"message": refusal}]}, final("next answer")])
    monkeypatch.setattr(cli, "ModelClient", lambda *a, **kw: client)
    monkeypatch.setenv("AGENT_API_KEY", "synthetic-offline-key")
    inputs = iter(["first", "second", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert (
        cli.main(
            [
                "--workspace",
                str(tmp_path),
                "--session-dir",
                str(tmp_path / "sessions"),
                "--session-id",
                "refusal",
            ]
        )
        == 0
    )
    assert client.requests[1][-2] == refusal
    output = capsys.readouterr().out
    assert "Cannot help." in output and "next answer" in output


@pytest.mark.parametrize("interrupted", [False, True])
def test_checkpoint_failure_has_one_completed_terminal_and_separate_error(
    tmp_path,
    monkeypatch,
    interrupted,
):
    from mini_agent.session import SessionError

    root = tmp_path / "sessions"
    with SessionStore(root).create(tmp_path, initial_messages(Policy(tmp_path)), "save") as s:
        before = s.messages

        def fail(messages):
            if interrupted:
                raise KeyboardInterrupt()
            raise SessionError("disk full")

        monkeypatch.setattr(s, "commit_messages", fail)
        error = KeyboardInterrupt if interrupted else SessionError
        match = None if interrupted else "Answer generated.*not saved"
        with pytest.raises(error, match=match):
            run_turn(
                "hello",
                FakeClient([final("answer")]),
                Policy(tmp_path),
                on_event=lambda _: None,
                session=s,
            )
        assert s.messages == before
    events = read_history(root, "save")
    assert [e["data"]["status"] for e in events if e["event"] == "turn_end"] == ["completed"]
    assert events[-1]["event"] == "checkpoint_error"
    with SessionStore(root).resume("save") as s:
        assert s.messages == before
