from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from mini_agent.model import ModelClient

RESPONSE = {"choices": [{"message": {"role": "assistant", "content": "offline"}}]}
MESSAGES = [{"role": "user", "content": "hello"}]
TOOLS = [{"type": "function", "function": {"name": "test", "parameters": {}}}]


def capture_requests(monkeypatch):
    requests = []

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps(RESPONSE).encode())

    monkeypatch.setattr("mini_agent.model.urllib.request.urlopen", urlopen)
    return requests


@pytest.mark.parametrize("tools", [[], TOOLS])
@pytest.mark.parametrize("explicit_none", [False, True])
def test_unconfigured_and_disabled_body_is_unchanged(monkeypatch, tools, explicit_none):
    requests = capture_requests(monkeypatch)
    kwargs = {"prompt_cache_key": None} if explicit_none else {}
    client = ModelClient("offline-placeholder", "https://example.invalid/v1", "test", **kwargs)

    assert client.complete(MESSAGES, tools) == RESPONSE

    assert len(requests) == 1
    request, timeout = requests[0]
    expected = {"model": "test", "messages": MESSAGES}
    if tools:
        expected.update(tools=tools, tool_choice="auto")
    assert json.loads(request.data) == expected
    assert request.data == json.dumps(expected).encode()
    assert request.full_url == "https://example.invalid/v1/chat/completions"
    assert request.method == "POST"
    assert timeout == 90


def test_cache_key_stays_stable_across_calls_tools_and_recreated_client(monkeypatch):
    requests = capture_requests(monkeypatch)
    key = "Session_01-resumed"
    client = ModelClient(
        "offline-placeholder", "https://example.invalid/v1/", "test", prompt_cache_key=key
    )
    messages = list(MESSAGES)
    assert client.complete(messages, TOOLS) == RESPONSE
    messages = [*messages, {"role": "assistant", "content": "first"}]
    assert client.complete(messages, []) == RESPONSE
    resumed = ModelClient(
        "offline-placeholder", "https://example.invalid/v1/", "test", prompt_cache_key=key
    )
    assert resumed.complete(messages, TOOLS) == RESPONSE

    assert len(requests) == 3
    bodies = [json.loads(request.data) for request, _ in requests]
    assert [body["prompt_cache_key"] for body in bodies] == [key] * 3
    assert bodies[0]["messages"] == MESSAGES
    assert bodies[1]["messages"] == messages
    assert bodies[2]["messages"] == messages
    assert set(bodies[0]) == {"model", "messages", "tools", "tool_choice", "prompt_cache_key"}
    assert set(bodies[1]) == {"model", "messages", "prompt_cache_key"}
    assert all(request.full_url.endswith("/v1/chat/completions") for request, _ in requests)


@pytest.mark.parametrize("key", ["a", "A0_-", "x" * 64])
def test_valid_cache_keys_are_sent(monkeypatch, key):
    requests = capture_requests(monkeypatch)
    client = ModelClient(
        "offline-placeholder", "https://example.invalid", "test", prompt_cache_key=key
    )
    client.complete(MESSAGES, [])
    assert json.loads(requests[0][0].data)["prompt_cache_key"] == key


@pytest.mark.parametrize(
    "key", ["", "a" * 65, "a.b", "a/b", "a\\b", "has space", "a\n", "é", "Ａ", 12, False]
)
def test_invalid_cache_keys_fail_before_http(monkeypatch, key):
    requests = capture_requests(monkeypatch)
    with pytest.raises(ValueError, match="1-64 ASCII"):
        ModelClient("offline-placeholder", "https://example.invalid", "test", prompt_cache_key=key)
    assert requests == []


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        (400, b'{"error":{"message":"Unrecognized request argument: prompt_cache_key"}}'),
        (422, b'{"error":"extra inputs are not permitted"}'),
        (409, b'{"error":"prompt_cache_key is unsupported"}'),
    ],
)
def test_cache_rejection_gives_opt_out_hint_without_retry_or_endpoint_change(status, detail):
    url = "https://example.invalid/custom/chat/completions"
    error = urllib.error.HTTPError(url, status, "Rejected", {}, io.BytesIO(detail))
    client = ModelClient(
        "offline-placeholder",
        "https://example.invalid/custom",
        "test",
        prompt_cache_key="session-1",
    )
    with patch("mini_agent.model.urllib.request.urlopen", side_effect=error) as urlopen:
        with pytest.raises(RuntimeError, match="--no-prompt-cache") as raised:
            client.complete(MESSAGES, [])

    assert f"model API error {status}" in str(raised.value)
    assert raised.value.__cause__ is error
    urlopen.assert_called_once()
    request = urlopen.call_args.args[0]
    assert request.full_url == url
    assert json.loads(request.data) == {
        "model": "test",
        "messages": MESSAGES,
        "prompt_cache_key": "session-1",
    }


def test_disabled_cache_preserves_http_error_and_sends_once():
    error = urllib.error.HTTPError(
        "https://example.invalid/chat/completions", 400, "Rejected", {}, io.BytesIO(b"bad request")
    )
    client = ModelClient("offline-placeholder", "https://example.invalid", "test")
    with patch("mini_agent.model.urllib.request.urlopen", side_effect=error) as urlopen:
        with pytest.raises(RuntimeError, match="^model API error 400: bad request$"):
            client.complete(MESSAGES, [])
    urlopen.assert_called_once()
    assert "prompt_cache_key" not in json.loads(urlopen.call_args.args[0].data)


def test_connection_failure_does_not_retry_or_change_endpoint():
    client = ModelClient(
        "offline-placeholder", "https://example.invalid/v1", "test", prompt_cache_key="session-1"
    )
    error = urllib.error.URLError("offline failure")
    with patch("mini_agent.model.urllib.request.urlopen", side_effect=error) as urlopen:
        with pytest.raises(RuntimeError, match="model API connection failed"):
            client.complete(MESSAGES, [])
    urlopen.assert_called_once()
    assert urlopen.call_args.args[0].full_url == "https://example.invalid/v1/chat/completions"
