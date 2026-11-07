"""Offline regression tests for sanitising damaged text before JSON encoding."""

import json
import logging
from pathlib import Path

from mini_agent import loop, text_safety
from mini_agent.loop import run_turn
from mini_agent.model import ModelClient
from mini_agent.text_safety import replace_unpaired_surrogates
from mini_agent.tool_execution_context import Policy, ToolExecutionContext
from mini_agent.tool_registry import ToolRegistry
from mini_agent.tools.list_directory import ListDirectoryTool

ASTRAL_PAIR = "\ud83d\ude00"
ASTRAL_CHARACTER = "\U0001f600"
LONE_SURROGATES = "\udca3\udcac"


class FakeResponse:
    """Minimal urllib response returning one JSON payload."""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        """Enter the context manager.

        Args:
            None.

        Returns:
            This response.
        """
        return self

    def __exit__(self, *exc_info):
        """Exit the context manager without suppressing exceptions.

        Args:
            *exc_info: Exception details supplied by the context manager protocol.

        Returns:
            False, so exceptions propagate.
        """
        return False

    def read(self):
        """Return the encoded payload bytes.

        Args:
            None.

        Returns:
            UTF-8 encoded JSON.
        """
        return json.dumps(self._payload).encode()


def test_replaces_lone_surrogates_and_keeps_valid_pairs():
    """Replace unpaired code points but keep a high/low surrogate pair."""
    cleaned, replaced = replace_unpaired_surrogates("a" + LONE_SURROGATES + "b")
    assert cleaned == "a\ufffd\ufffdb"
    assert replaced == 2

    kept, count = replace_unpaired_surrogates(ASTRAL_PAIR)
    assert kept == ASTRAL_PAIR
    assert count == 0
    assert json.dumps(kept) == '"\\ud83d\\ude00"'


def test_sanitize_reports_fields_without_mutating_input():
    """Report the damaged field path and never mutate the caller's payload."""
    payload = {
        "messages": [
            {"role": "user", "content": "x\udca3"},
            {"role": "tool", "content": "ok"},
        ]
    }
    cleaned, findings = text_safety.sanitize(payload)
    assert findings == [("payload.messages[0].content", 1)]
    assert cleaned["messages"][0]["content"] == "x\ufffd"
    assert payload["messages"][0]["content"] == "x\udca3"
    assert cleaned is not payload


def test_json_size_accepts_unpaired_surrogates():
    """Budget accounting measures the sanitised form instead of raising."""
    value = {"path": "a" + LONE_SURROGATES}
    expected = len(json.dumps({"path": "a\ufffd\ufffd"}, ensure_ascii=False).encode())
    assert text_safety.json_size(value) == expected


def test_request_body_is_valid_and_warning_hides_content(monkeypatch, caplog):
    """Send only valid JSON, keep the wire ASCII-only, and never log content."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["data"] = request.data
        return FakeResponse({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = ModelClient("k", "http://example.invalid/v1", "m")
    with caplog.at_level(logging.WARNING, logger="mini_agent.text_safety"):
        client.complete([{"role": "user", "content": "read secret.md \udca3"}], [])

    raw = captured["data"]
    raw.decode("ascii")
    body = json.loads(raw)
    assert body["messages"][0]["content"] == "read secret.md \ufffd"
    assert "\\udca3" not in raw.decode()

    message = caplog.records[-1].getMessage()
    assert "messages[0].content (1)" in message
    assert "secret.md" not in message


def test_clean_non_ascii_is_preserved_without_warning(monkeypatch, caplog):
    """Leave valid multilingual text untouched and stay silent."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["data"] = json.loads(request.data)
        return FakeResponse({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = ModelClient("k", "http://example.invalid/v1", "m")
    with caplog.at_level(logging.WARNING, logger="mini_agent.text_safety"):
        client.complete([{"role": "user", "content": "中文 " + ASTRAL_PAIR}], [])

    assert captured["data"]["messages"][0]["content"] == "中文 " + ASTRAL_CHARACTER
    assert caplog.records == []


def test_tool_result_with_damaged_name_is_sanitized(monkeypatch, caplog, tmp_path):
    """Sanitise tool output that carries a non-UTF-8 file name."""

    class StubRegistry:
        """Return one damaged directory entry without touching the filesystem."""

        def schemas(self):
            """Return no tool schemas.

            Args:
                None.

            Returns:
                Empty list.
            """
            return []

        def execute(self, name, arguments, context):
            """Ignore the request and return a surrogate-bearing entry list.

            Args:
                name: Tool name.
                arguments: Validated arguments.
                context: Execution context.

            Returns:
                Tool result mapping.
            """
            return {"ok": True, "entries": ["\udca3bad.txt"]}

    monkeypatch.setattr(
        loop, "ToolRegistry", type("Stub", (), {"discover": staticmethod(StubRegistry)})
    )
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["data"] = json.loads(request.data)
        return FakeResponse({"choices": [{"message": {"role": "assistant", "content": "done"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = ModelClient("k", "http://example.invalid/v1", "m")

    class ToolThenModel:
        """Ask for one tool call, then serialize the follow-up through the client."""

        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools):
            """Return a tool call first and a sanitized request afterwards.

            Args:
                messages: Conversation so far.
                tools: Advertised tool schemas.

            Returns:
                Model response mapping.
            """
            self.calls += 1
            if self.calls == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": json.dumps({"path": "a.txt"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            return client.complete(messages, tools)

    with caplog.at_level(logging.WARNING, logger="mini_agent.text_safety"):
        run_turn("list files", ToolThenModel(), Policy(tmp_path), on_event=lambda _: None)

    tool_message = captured["data"]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert "\\udca3" not in json.dumps(tool_message)
    assert "\ufffd" in tool_message["content"]
    record = caplog.records[-1].getMessage()
    assert "tool result read_file.entries[0] (1)" in record
    assert "outbound request" not in caplog.text


def test_warning_is_emitted_once_when_history_is_resent(monkeypatch, caplog, tmp_path):
    """Store sanitized text, so later steps of the turn do not warn again."""

    class StubRegistry:
        """Return a damaged entry once, then a clean one."""

        def __init__(self):
            self.calls = 0

        def schemas(self):
            """Return no tool schemas.

            Args:
                None.

            Returns:
                Empty list.
            """
            return []

        def execute(self, name, arguments, context):
            """Return damaged output first and clean output afterwards.

            Args:
                name: Tool name.
                arguments: Validated arguments.
                context: Execution context.

            Returns:
                Tool result mapping.
            """
            self.calls += 1
            entries = ["\udca3bad.txt"] if self.calls == 1 else []
            return {"ok": True, "entries": entries}

    registry = StubRegistry()
    monkeypatch.setattr(
        loop, "ToolRegistry", type("Stub", (), {"discover": staticmethod(lambda: registry)})
    )
    requests = []

    def fake_urlopen(request, timeout=None):
        requests.append(json.loads(request.data))
        return FakeResponse({"choices": [{"message": {"role": "assistant", "content": "done"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = ModelClient("k", "http://example.invalid/v1", "m")

    class ThreeStepModel:
        """Ask for two tool calls before answering, forcing a history resend."""

        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools):
            """Return tool calls twice, then delegate the real request.

            Args:
                messages: Conversation so far.
                tools: Advertised tool schemas.

            Returns:
                Model response mapping.
            """
            self.calls += 1
            if self.calls <= 2:
                return _tool_call_response(self.calls)
            return client.complete(messages, tools)

    with caplog.at_level(logging.WARNING, logger="mini_agent.text_safety"):
        run_turn("list files", ThreeStepModel(), Policy(tmp_path), on_event=lambda _: None)

    # Two synthetic tool steps, then one real request resending the whole history.
    assert len(requests) == 1
    body = requests[0]
    assert "\\udca3" not in json.dumps(body)
    assert "\ufffd" in body["messages"][3]["content"]
    assert [record.getMessage() for record in caplog.records] == [
        "replaced 1 unpaired surrogate(s) with U+FFFD in tool result read_file.entries[0] (1)"
    ]


def _tool_call_response(number: int) -> dict:
    """Build one assistant tool-call response for read_file.

    Args:
        number: One-based step number used for the call id.

    Returns:
        Model response mapping.
    """
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": f"c{number}",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "a.txt"}),
                            },
                        }
                    ],
                }
            }
        ]
    }


def test_list_directory_sanitizes_damaged_names(monkeypatch, tmp_path):
    """Replace surrogates in directory entries before returning them to the model."""

    class FakeEntry:
        """Stand in for a directory entry whose name is not valid UTF-8."""

        name = "\udca3bad.txt"

    monkeypatch.setattr(Path, "iterdir", lambda self: iter([FakeEntry()]))
    registry = ToolRegistry([ListDirectoryTool()])
    context = ToolExecutionContext(Policy(tmp_path), lambda _: False)
    result = registry.execute("list_directory", {"path": "."}, context)
    assert result == {"ok": True, "entries": ["\ufffdbad.txt"]}
