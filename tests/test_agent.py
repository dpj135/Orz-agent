import json
from pathlib import Path

from mini_agent.loop import run_turn
from mini_agent.tools import Policy, execute


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append(json.loads(json.dumps(messages)))
        return next(self.responses)


def call(name, args, call_id="c1"):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                }
            }
        ]
    }


def test_loop_tool_then_final(tmp_path: Path):
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    client = FakeClient(
        [
            call("read_file", {"path": "note.txt"}),
            {"choices": [{"message": {"role": "assistant", "content": "It says hello."}}]},
        ]
    )
    answer = run_turn("summarize note", client, Policy(tmp_path), on_event=lambda _: None)
    assert answer == "It says hello."
    assert len(client.requests) == 2
    assert client.requests[1][-1]["role"] == "tool"


def test_workspace_escape_denied(tmp_path: Path):
    result = execute("read_file", {"path": "../secret.txt"}, Policy(tmp_path), lambda _: False)
    assert result["ok"] is False
    assert "outside" in result["error"]


def test_write_requires_policy_and_confirmation(tmp_path: Path):
    result = execute(
        "write_file", {"path": "a.txt", "content": "x"}, Policy(tmp_path), lambda _: True
    )
    assert result["ok"] is False
    assert not (tmp_path / "a.txt").exists()
