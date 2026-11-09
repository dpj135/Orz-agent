"""Loopback protocol validator and real CLI process harness (no product imports)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
PROCESS_TIMEOUT_SECONDS = 20
STARTUP_TIMEOUT_SECONDS = 5
MAX_REQUEST_BYTES = 2 * 1024 * 1024


def reject_surrogates(value: Any) -> None:
    """Reject surrogate code points anywhere in a decoded JSON document."""
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("unpaired surrogate in request JSON")
    elif isinstance(value, list):
        for item in value:
            reject_surrogates(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            reject_surrogates(key)
            reject_surrogates(item)


def validate_request(body: Any) -> None:
    """Check the Chat Completions subset exercised here, independently of client code."""
    reject_surrogates(body)
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        raise ValueError("model must be a string in a JSON object")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a nonempty array")
    pending_calls: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("message must be an object")
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("unsupported message role")
        calls = message.get("tool_calls")
        content = message.get("content")
        if not isinstance(content, str) and not (role == "assistant" and calls):
            raise ValueError("message content must be a string (or tool-call null)")
        if calls:
            if role != "assistant" or not isinstance(calls, list):
                raise ValueError("tool_calls must be an assistant array")
            for call in calls:
                if not isinstance(call, dict) or call.get("type") != "function":
                    raise ValueError("tool call must be a function")
                function = call.get("function")
                call_id = call.get("id")
                if not isinstance(call_id, str) or call_id in pending_calls:
                    raise ValueError("tool call needs a unique string id")
                if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                    raise ValueError("tool call needs a function name")
                # Deliberately do NOT parse arguments: invalid model arguments are tested.
                if not isinstance(function.get("arguments"), str):
                    raise ValueError("tool arguments must be a JSON-encoded string")
                pending_calls.add(call_id)
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending_calls:
                raise ValueError("tool result must reference a preceding call")
            pending_calls.remove(call_id)
    if pending_calls:
        raise ValueError("request contains unanswered tool calls")
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError("tools must be a nonempty array")
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise ValueError("tool definition must have type=function")
        function = tool.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ValueError("tool definition needs a function name")
        parameters = function.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError("tool parameters must be an object schema")
        if not isinstance(parameters.get("properties"), dict):
            raise ValueError("tool parameters need properties")
        required = parameters.get("required")
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise ValueError("tool parameters need a required array")
    if body.get("tool_choice") not in {"auto", "none", "required"}:
        raise ValueError("tool_choice must be a supported string")


def decode_request(raw: bytes) -> dict[str, Any]:
    """Decode strict UTF-8 JSON, then validate the exercised protocol subset."""
    body = json.loads(raw.decode("utf-8"))
    validate_request(body)
    return body


def completion_fixture(name: str = "completion") -> dict[str, Any]:
    """Load an explicitly synthetic protocol fixture afresh for every scenario."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def tool_response(name: str, arguments: Any) -> dict[str, Any]:
    response = completion_fixture("tool_call")
    function = response["choices"][0]["message"]["tool_calls"][0]["function"]
    function["name"] = name
    function["arguments"] = json.dumps(arguments)
    return response


@dataclass
class Reply:
    body: dict[str, Any]
    status: int = 200


class MockLLM:
    """A finite response script; unexpected or malformed requests fail the test."""

    def __init__(self, replies: list[Reply], artifacts: Path):
        self.replies = replies
        self.artifacts = artifacts
        self.requests: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.consumed = 0
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                # Do not retain HTTP headers or credentials in the artifacts.
                pass

            def send_json(self, status: int, body: dict[str, Any]) -> None:
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                self.send_json(200 if self.path == "/health" else 404, {"ready": True})

            def do_POST(self) -> None:
                with owner.lock:
                    try:
                        if self.path != "/v1/chat/completions":
                            raise ValueError("unexpected HTTP route")
                        if not self.headers.get("Authorization", "").startswith("Bearer "):
                            raise ValueError("missing Bearer Authorization")
                        if self.headers.get_content_type() != "application/json":
                            raise ValueError("expected application/json")
                        size = int(self.headers.get("Content-Length", "0"))
                        if not 0 < size <= MAX_REQUEST_BYTES:
                            raise ValueError("invalid request size")
                        raw = self.rfile.read(size)
                        index = len(owner.requests)
                        (owner.artifacts / f"request-{index}.json").write_bytes(raw)
                        body = decode_request(raw)
                        owner.requests.append(body)
                        if owner.consumed >= len(owner.replies):
                            raise ValueError("unexpected extra model request")
                        reply = owner.replies[owner.consumed]
                        owner.consumed += 1
                    except (ValueError, TypeError, KeyError) as exc:
                        owner.errors.append(str(exc))
                        self.send_json(400, {"error": {"message": str(exc)}})
                    else:
                        self.send_json(reply.status, reply.body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            try:
                with opener.open(self.base_url.removesuffix("/v1") + "/health", timeout=1):
                    return
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("mock server readiness timeout") from None
                time.sleep(0.01)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=STARTUP_TIMEOUT_SECONDS)
        (self.artifacts / "server-errors.json").write_text(
            json.dumps(self.errors, indent=2), encoding="utf-8"
        )
        assert not self.thread.is_alive(), "mock server thread did not stop"


@pytest.fixture
def mock_llm(tmp_path: Path) -> Iterator[Callable[[list[Reply]], MockLLM]]:
    servers: list[MockLLM] = []

    def create(replies: list[Reply]) -> MockLLM:
        directory = tmp_path / f"http-{len(servers)}"
        directory.mkdir()
        server = MockLLM(replies, directory)
        servers.append(server)
        server.start()
        return server

    yield create
    for server in servers:
        server.close()
    for server in servers:
        assert not server.errors, f"mock protocol errors: {server.errors}"
        assert server.consumed == len(server.replies), "not all scripted replies were consumed"


def kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill the isolated CLI group, including descendants, after timeout."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=STARTUP_TIMEOUT_SECONDS,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


@pytest.fixture
def cli(tmp_path: Path) -> Callable[..., subprocess.CompletedProcess[str]]:
    count = 0

    def run(
        server: MockLLM | None,
        *,
        prompt: str | None = "Synthetic test prompt",
        stdin: str | bytes = "",
        flags: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        nonlocal count
        workspace = tmp_path / "workspace"
        workspace.mkdir(exist_ok=True)
        command = [sys.executable, "-m", "mini_agent"]
        if prompt is not None:
            command.append(prompt)
        command += ["--workspace", str(workspace), "--model", "synthetic-e2e-model"]
        if server is not None:
            command += ["--base-url", server.base_url, "--api-key", "dummy"]
        command += list(flags)
        environment = os.environ.copy()
        for name in list(environment):
            if name.upper().startswith("AGENT_"):
                environment.pop(name)
        # Enforce predictable decoding even on Windows. surrogatepass lets the
        # regression inject a real surrogate through stdin, not a product mock.
        environment.update(
            PYTHONIOENCODING="utf-8:surrogatepass",
            PYTHONUTF8="1",
            NO_PROXY="*",
            no_proxy="*",
        )
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(
            command,
            cwd=REPOSITORY,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **options,
        )
        raw_input = stdin if isinstance(stdin, bytes) else stdin.encode("utf-8")
        timed_out = False
        try:
            stdout, stderr = process.communicate(raw_input, timeout=PROCESS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_process_tree(process)
            stdout, stderr = process.communicate(timeout=STARTUP_TIMEOUT_SECONDS)
        finally:
            if process.poll() is None:
                kill_process_tree(process)
                process.wait(timeout=STARTUP_TIMEOUT_SECONDS)
        prefix = tmp_path / f"cli-{count}"
        count += 1
        prefix.with_suffix(".stdout.txt").write_bytes(stdout)
        prefix.with_suffix(".stderr.txt").write_bytes(stderr)
        prefix.with_suffix(".status.json").write_text(
            json.dumps({"returncode": process.returncode, "timed_out": timed_out}), encoding="utf-8"
        )
        assert not timed_out, f"CLI exceeded {PROCESS_TIMEOUT_SECONDS}s; see {prefix} artifacts"
        return subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )

    return run
