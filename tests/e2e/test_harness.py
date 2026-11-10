"""Regression tests for the mock server's diagnostic and lifecycle behavior."""

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from .conftest import MockLLM, Reply, completion_fixture


@pytest.mark.parametrize("bad", [b"not-json", b'{"messages":[{"content":"\\ud800"}]}'])
def test_rejected_request_does_not_overwrite_diagnostics(tmp_path: Path, bad: bytes) -> None:
    good = json.dumps(
        {
            "model": "synthetic-model",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "synthetic",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    },
                }
            ],
            "tool_choice": "auto",
        }
    ).encode()
    server = MockLLM([Reply(completion_fixture())], tmp_path)
    server.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(raw: bytes) -> int:
        request = urllib.request.Request(
            server.base_url + "/chat/completions",
            data=raw,
            headers={"Authorization": "Bearer dummy", "Content-Type": "application/json"},
        )
        with opener.open(request, timeout=5) as response:
            return response.status

    try:
        with pytest.raises(urllib.error.HTTPError) as rejected:
            post(bad)
        assert rejected.value.code == 400
        rejected.value.close()
        assert post(good) == 200
    finally:
        server.close()
    assert server.received == 2
    assert len(server.requests) == 1
    assert len(server.errors) == 1
    assert server.consumed == 1
    assert (tmp_path / "request-0.json").read_bytes() == bad
    assert (tmp_path / "request-1.json").read_bytes() == good


def test_readiness_failure_closes_server(mock_llm, monkeypatch: pytest.MonkeyPatch) -> None:
    started = []
    original = MockLLM.start

    def fail_after_start(server: MockLLM) -> None:
        original(server)
        started.append(server)
        raise RuntimeError("synthetic readiness failure")

    monkeypatch.setattr(MockLLM, "start", fail_after_start)
    with pytest.raises(RuntimeError, match="synthetic readiness failure"):
        mock_llm([])
    assert len(started) == 1
    assert not started[0].thread.is_alive()
    assert started[0].server.fileno() == -1
