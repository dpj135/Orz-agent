import json
import urllib.error

import pytest

from mini_agent.search_client import SearchClient
from mini_agent.tool_execution_context import Policy, ToolExecutionContext
from mini_agent.tool_registry import ToolRegistry


def ctx(root, **kwargs):
    return ToolExecutionContext(Policy(root, **kwargs), lambda _: True)


def call(root, args, monkeypatch, **kwargs):
    monkeypatch.setenv("TAVILY_API_KEY", "secret-not-output")
    return ToolRegistry.discover().execute("web_search", args, ctx(root, **kwargs))


def response(payload):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return json.dumps(payload).encode()

    return FakeResponse()


def test_search_request_and_normalized_sources(tmp_path, monkeypatch):
    requests = []

    def fake_open(self, request, timeout):
        requests.append((request, timeout))
        return response(
            {
                "results": [
                    {"url": "https://example.com/a", "title": "A", "content": "snippet"},
                    {"url": "https://example.com/a", "title": "duplicate", "content": "x"},
                ]
            }
        )

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
    result = call(
        tmp_path, {"query": "Python", "max_results": 3}, monkeypatch, allow_web_search=True
    )
    assert result == {
        "ok": True,
        "provider": "tavily",
        "results": [{"url": "https://example.com/a", "title": "A", "snippet": "snippet"}],
        "truncated": False,
    }
    request, timeout = requests[0]
    assert timeout == 20
    assert request.get_header("Authorization") == "Bearer secret-not-output"
    assert "secret-not-output" not in json.dumps(result)
    assert json.loads(request.data)["query"] == "Python"


@pytest.mark.parametrize(
    "args",
    [{}, {"query": "  "}, {"query": "x", "max_results": 11}, {"query": "x", "max_results": True}],
)
def test_search_validation(tmp_path, args):
    result = ToolRegistry.discover().execute("web_search", args, ctx(tmp_path))
    assert not result["ok"]


def test_search_requires_opt_in_and_key(tmp_path, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    result = ToolRegistry.discover().execute("web_search", {"query": "x"}, ctx(tmp_path))
    assert not result["ok"]
    result = ToolRegistry.discover().execute(
        "web_search", {"query": "x"}, ctx(tmp_path, allow_web_search=True)
    )
    assert not result["ok"]
    assert "TAVILY_API_KEY" in result["error"]


def test_search_http_error_does_not_leak_body(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.tavily.com/search", 429, "busy", {}, None)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fail)
    result = call(tmp_path, {"query": "x"}, monkeypatch, allow_web_search=True)
    assert not result["ok"]
    assert "429" in result["error"]
    assert "secret" not in result["error"]


def test_search_redirect_is_not_followed(tmp_path, monkeypatch):
    from mini_agent.search_client import NoRedirect

    assert (
        NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://untrusted.example")
        is None
    )

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.tavily.com/search", 302, "redirect", {}, None)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fail)
    result = call(tmp_path, {"query": "x"}, monkeypatch, allow_web_search=True)
    assert not result["ok"]


def test_search_does_not_accept_non_http_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open",
        lambda *args, **kwargs: response({"results": [{"url": "file:///secret", "title": "x"}]}),
    )
    result = call(tmp_path, {"query": "x"}, monkeypatch, allow_web_search=True)
    assert result["ok"] and result["results"] == [] and result["truncated"]


def test_refusal_never_sends_request(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden)
    context = ToolExecutionContext(Policy(tmp_path, allow_web_search=True), lambda _: False)
    result = ToolRegistry.discover().execute("web_search", {"query": "private"}, context)
    assert not result["ok"]


@pytest.mark.parametrize("payload", [{}, {"results": "bad"}, {"results": [None]}])
def test_malformed_response(tmp_path, monkeypatch, payload):
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **k: response(payload))
    result = call(tmp_path, {"query": "x"}, monkeypatch, allow_web_search=True)
    assert not result["ok"]


def test_budget_and_empty_results(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open",
        lambda *a, **k: response(
            {"results": [{"url": "https://example.com", "content": "a" * 5000}]}
        ),
    )
    result = SearchClient("test-only").search("x", 5, 20)
    assert result["results"] == [] and result["truncated"]
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open", lambda *a, **k: response({"results": []})
    )
    assert SearchClient("test-only").search("x", 5, 100)["results"] == []


def test_oversized_response_rejected(monkeypatch):
    from mini_agent.search_client import MAX_RESPONSE_BYTES

    class Large:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size == MAX_RESPONSE_BYTES + 1
            return b"x" * size

    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **k: Large())
    with pytest.raises(ValueError, match="exceeds"):
        SearchClient("test-only").search("x", 5, 100)
