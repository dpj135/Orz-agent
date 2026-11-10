import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mini_agent import cli
from mini_agent.loop import initial_messages, run_turn
from mini_agent.session import SessionStore
from mini_agent.tools import Policy


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append(copy.deepcopy(messages))
        result = next(self.responses)
        if isinstance(result, BaseException):
            raise result
        return result


def final(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def tool_call(arguments='{"path":"note.txt"}'):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            }
        ]
    }


def read_history(root, session_id):
    path = root / hashlib.sha256(session_id.encode("ascii")).hexdigest() / "history.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_multi_turn_resume_and_independent_history(tmp_path):
    root = tmp_path / "sessions"
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "note.txt").write_text("hello", encoding="utf-8")
    store = SessionStore(root)
    policy = Policy(workspace)
    with store.create(workspace, initial_messages(policy), "task") as session:
        client = FakeClient([tool_call(), final("hello"), final("second")])
        run_turn("read", client, policy, on_event=lambda _: None, session=session)
        prefix = session.messages
        run_turn("again", client, policy, on_event=lambda _: None, session=session)
        assert client.requests[2][:-1] == prefix
        saved = session.messages
        # A future compression changes model context, not historical events.
        history_before = read_history(root, "task")
        session.commit_messages([saved[0], saved[-1]])
        assert read_history(root, "task") == history_before
    with store.resume("task") as session:
        client = FakeClient([final("resumed")])
        run_turn("continue", client, policy, on_event=lambda _: None, session=session)
        assert client.requests[0][:-1] == [saved[0], saved[-1]]
        assert session.workspace == workspace.resolve()
    history = read_history(root, "task")
    serialized = json.dumps(history)
    assert "read_file" in serialized and "hello" in serialized and "call_1" in serialized
    assert len(history) > 3


@pytest.mark.parametrize("failure", [RuntimeError("offline failure"), KeyboardInterrupt()])
def test_failed_turn_keeps_checkpoint_but_logs_interactions(tmp_path, failure):
    root = tmp_path / "sessions"
    store = SessionStore(root)
    with store.create(tmp_path, initial_messages(Policy(tmp_path)), "task") as session:
        before = session.messages
        with pytest.raises(type(failure)):
            run_turn(
                "unfinished",
                FakeClient([failure]),
                Policy(tmp_path),
                on_event=lambda _: None,
                session=session,
            )
        assert session.messages == before
    with store.resume("task") as session:
        assert session.messages == before
    text = json.dumps(read_history(root, "task"))
    assert "unfinished" in text and "turn_end" in text


def test_max_steps_and_invalid_tool_arguments_are_logged(tmp_path):
    root = tmp_path / "sessions"
    with SessionStore(root).create(tmp_path, initial_messages(Policy(tmp_path)), "task") as s:
        before = s.messages
        with pytest.raises(RuntimeError, match="max_steps"):
            run_turn(
                "read",
                FakeClient([tool_call("invalid")]),
                Policy(tmp_path),
                max_steps=1,
                on_event=lambda _: None,
                session=s,
            )
        assert s.messages == before
    assert "tool_rejected" in json.dumps(read_history(root, "task"))


def test_cli_create_resume_and_mutual_exclusion(tmp_path, monkeypatch, capsys):
    root = tmp_path / "sessions"
    work = tmp_path / "work"
    work.mkdir()
    clients = []

    def client_factory(*args, **kwargs):
        client = FakeClient([final("answer")])
        client.cache_key = kwargs.get("prompt_cache_key")
        clients.append(client)
        return client

    monkeypatch.setattr(cli, "ModelClient", client_factory)
    monkeypatch.setenv("AGENT_API_KEY", "fake-offline-key")
    monkeypatch.setenv("AGENT_SESSION_ID", "ignored-legacy-variable")
    base = ["--session-dir", str(root)]
    assert cli.main(base + ["--workspace", str(work), "--session-id", "task", "first"]) == 0
    assert "task (new)" in capsys.readouterr().out
    assert clients[0].cache_key == "task"
    prefix = clients[0].requests[0] + [{"role": "assistant", "content": "answer"}]
    assert cli.main(base + ["--resume", "task", "second"]) == 0
    assert clients[1].requests[0][:-1] == prefix
    assert clients[1].cache_key == "task"
    assert "task (resumed)" in capsys.readouterr().out
    assert cli.main(base + ["--resume", "task", "--no-prompt-cache", "third"]) == 0
    assert clients[2].cache_key is None
    count = len(clients)
    assert cli.main(base + ["--session-id", "task", "duplicate"]) == 1
    assert cli.main(base + ["--resume", "missing", "missing"]) == 1
    assert cli.main(base + ["--resume", "task", "--workspace", str(tmp_path), "bad"]) == 1
    assert len(clients) == count
    with pytest.raises(SystemExit):
        cli.main(base + ["--session-id", "a", "--resume", "b"])


def test_repl_retains_history_between_inputs(tmp_path, monkeypatch):
    client = FakeClient([final("one"), final("two")])
    monkeypatch.setattr(cli, "ModelClient", lambda *a, **kw: client)
    monkeypatch.setenv("AGENT_API_KEY", "fake")
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
                "repl",
            ]
        )
        == 0
    )
    assert client.requests[1][:-1] == (
        client.requests[0] + [{"role": "assistant", "content": "one"}]
    )


@pytest.mark.parametrize("failure_at", ["output", "history", "checkpoint"])
def test_finalization_failure_keeps_old_context(tmp_path, monkeypatch, failure_at):
    from mini_agent.session import SessionError

    root = tmp_path / "sessions"
    with SessionStore(root).create(tmp_path, initial_messages(Policy(tmp_path)), "failure") as s:
        before = s.messages
        record = s.record

        def output(text):
            if failure_at == "output":
                raise RuntimeError("output failed")

        def record_event(event, **data):
            if failure_at == "history" and event == "turn_end":
                raise SessionError("history unavailable")
            record(event, **data)

        def save(messages):
            raise SessionError("checkpoint unavailable")

        monkeypatch.setattr(s, "record", record_event)
        if failure_at == "checkpoint":
            monkeypatch.setattr(s, "commit_messages", save)
        with pytest.raises((RuntimeError, SessionError)):
            run_turn(
                "hello", FakeClient([final("answer")]), Policy(tmp_path), on_event=output, session=s
            )
        assert s.messages == before
    with SessionStore(root).resume("failure") as s:
        assert s.messages == before


def test_failure_after_tool_is_not_replayed(tmp_path):
    root = tmp_path / "sessions"
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    with SessionStore(root).create(tmp_path, initial_messages(Policy(tmp_path)), "tool") as s:
        before = s.messages
        with pytest.raises(RuntimeError, match="after tool"):
            run_turn(
                "read",
                FakeClient([tool_call(), RuntimeError("after tool")]),
                Policy(tmp_path),
                on_event=lambda _: None,
                session=s,
            )
        assert s.messages == before
    events = read_history(root, "tool")
    assert [item["event"] for item in events] == [
        "user_input",
        "llm_response",
        "tool_start",
        "tool_end",
        "turn_end",
    ]
    assert events[-1]["data"]["status"] == "failed"


def test_logging_failure_preserves_original_error(tmp_path, monkeypatch):
    from mini_agent.session import SessionError

    with SessionStore(tmp_path / "sessions").create(
        tmp_path,
        initial_messages(Policy(tmp_path)),
        "logging",
    ) as s:
        original = s.record

        def record(event, **data):
            if event == "turn_end":
                raise SessionError("log broken")
            original(event, **data)

        monkeypatch.setattr(s, "record", record)
        with pytest.raises(RuntimeError, match="original failure"):
            run_turn(
                "hello",
                FakeClient([RuntimeError("original failure")]),
                Policy(tmp_path),
                on_event=lambda _: None,
                session=s,
            )


def test_process_cli_real_http_resume(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            reject = requests[-1]["messages"][-1].get("content") == "reject"
            response = json.dumps(
                {"error": "unknown prompt_cache_key"} if reject else final("loopback answer")
            ).encode()
            self.send_response(400 if reject else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    env = os.environ.copy()
    env["AGENT_API_KEY"] = "synthetic-loopback-only"
    base = [
        sys.executable,
        "-m",
        "mini_agent",
        "--session-dir",
        str(tmp_path / "sessions"),
        "--base-url",
        f"http://127.0.0.1:{server.server_port}/v1",
    ]
    try:
        for extra in [
            ["--session-id", "process", "--workspace", str(tmp_path), "first"],
            ["--resume", "process", "second"],
            ["--resume", "process", "--no-prompt-cache", "third"],
        ]:
            result = subprocess.run(
                base + extra,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        assert requests[0]["prompt_cache_key"] == requests[1]["prompt_cache_key"] == "process"
        assert "prompt_cache_key" not in requests[2]
        assert requests[1]["messages"][:-1] == requests[0]["messages"] + [
            {"role": "assistant", "content": "loopback answer"}
        ]
        duplicate = subprocess.run(
            base + ["--session-id", "process", "duplicate"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert duplicate.returncode == 1
        assert len(requests) == 3
        rejected = subprocess.run(
            base + ["--resume", "process", "reject"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert rejected.returncode == 1
        assert "--no-prompt-cache" in rejected.stdout
        assert len(requests) == 4
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
