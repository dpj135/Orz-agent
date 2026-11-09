"""Offline tests for sanitizing damaged user input before it reaches the model."""

from __future__ import annotations

import logging

from mini_agent.loop import run_turn
from mini_agent.text_safety import sanitize_user_input
from mini_agent.tool_execution_context import Policy

LONE_SURROGATES = "\udca3\udcac"


def test_replaces_lone_surrogates():
    """Unpaired surrogates become one replacement character each."""
    assert sanitize_user_input("a" + LONE_SURROGATES + "b") == "a\ufffd\ufffdb"


def test_keeps_clean_text_unchanged():
    """Valid Chinese and astral characters are returned without a new string."""
    text = "阅读summary.md \U0001f600"
    assert sanitize_user_input(text) is text


def test_logs_count_without_text(caplog):
    """One warning records the count but never the damaged text."""
    with caplog.at_level(logging.WARNING, logger="mini_agent.text_safety"):
        sanitize_user_input("x" + LONE_SURROGATES)
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "2" in message
    assert LONE_SURROGATES not in message


def test_clean_input_logs_nothing(caplog):
    """Clean prompts stay silent."""
    with caplog.at_level(logging.WARNING, logger="mini_agent.text_safety"):
        sanitize_user_input("阅读summary.md")
    assert not caplog.records


def test_run_turn_sends_sanitized_prompt(tmp_path):
    """The prompt appended to messages is sanitized before the request is built."""
    captured: dict[str, list] = {}

    class FakeClient:
        def complete(self, messages, tools):
            captured["messages"] = [dict(message) for message in messages]
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    run_turn(
        "x" + LONE_SURROGATES,
        FakeClient(),
        Policy(workspace=tmp_path),
        on_event=lambda _: None,
    )
    assert captured["messages"][-1] == {"role": "user", "content": "x\ufffd\ufffd"}
