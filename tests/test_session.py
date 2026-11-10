from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
import stat
import uuid
from pathlib import Path
from typing import Any

import pytest

from mini_agent import session as storage
from mini_agent.session import SessionError, SessionStore

INITIAL = [{"role": "system", "content": "base"}]


def tool_messages() -> list[dict[str, Any]]:
    return INITIAL + [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "read", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "read", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "b", "name": "read", "content": "two"},
        {"role": "tool", "tool_call_id": "a", "name": "read", "content": "one"},
        {"role": "assistant", "content": "done"},
    ]


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


def test_initial_checkpoint_identity_resume_and_isolation(
    store: SessionStore, tmp_path: Path
) -> None:
    messages = copy.deepcopy(INITIAL)
    with store.create(tmp_path, messages) as first:
        assert str(uuid.UUID(first.session_id)) == first.session_id
        assert first.path.name == hashlib.sha256(first.session_id.encode()).hexdigest()
        assert first.workspace == tmp_path
        checkpoint = json.loads((first.path / "checkpoint.json").read_text())
        assert set(checkpoint) == storage._FIELDS
        assert checkpoint["messages"] == INITIAL
        messages[0]["content"] = "mutated"
        first.messages[0]["content"] = "also mutated"
        assert first.messages == INITIAL
        identity = first.session_id
        with store.create(tmp_path, INITIAL) as second:
            assert first.session_id != second.session_id
            second.commit_messages(INITIAL + [{"role": "assistant", "content": "second"}])
            assert first.messages == INITIAL
        first.commit_messages(tool_messages())
    with store.resume(identity) as resumed:
        assert resumed.messages == tool_messages()
        assert resumed.workspace == tmp_path
        assert resumed.created_at == checkpoint["created_at"]
        assert resumed.updated_at >= resumed.created_at


@pytest.mark.parametrize("identity", ["", "a" * 65, "../x", "a.b", "é", "x/y", "x\\y", "a\n", 1])
def test_invalid_ids_do_not_create_files(
    store: SessionStore, tmp_path: Path, identity: Any
) -> None:
    with pytest.raises(SessionError):
        store.create(tmp_path, INITIAL, identity)
    with pytest.raises(SessionError):
        store.resume(identity)
    assert not store.root.exists()


@pytest.mark.parametrize("identity", ["A", "a", "CON", "nul", "_", "-", "a" * 64])
def test_safe_encoding_and_strict_create(
    store: SessionStore, tmp_path: Path, identity: str
) -> None:
    with store.create(tmp_path, INITIAL, identity) as session:
        before = (session.path / "checkpoint.json").read_bytes()
        with pytest.raises(SessionError, match="already exists"):
            store.create(tmp_path, INITIAL, identity)
        assert (session.path / "checkpoint.json").read_bytes() == before
    with store.resume(identity):
        pass


def test_case_distinct_ids(store: SessionStore, tmp_path: Path) -> None:
    with store.create(tmp_path, INITIAL, "A") as upper:
        with store.create(tmp_path, INITIAL, "a") as lower:
            assert upper.path != lower.path


def test_missing_resume_does_not_create(store: SessionStore) -> None:
    with pytest.raises(SessionError) as error:
        store.resume("missing")
    assert "session 'missing' not found" in str(error.value)
    assert str(store.root) in str(error.value)
    assert hashlib.sha256(b"missing").hexdigest() not in str(error.value)
    assert not store.root.exists()


def test_history_is_flushed_independent_and_never_replayed(
    store: SessionStore, tmp_path: Path
) -> None:
    with store.create(tmp_path, INITIAL, "events") as session:
        checkpoint = session.path / "checkpoint.json"
        original = checkpoint.read_bytes()
        session.record("user_input", text="秘密\nline\ud800")
        history = session.path / "history.jsonl"
        entries = [json.loads(line) for line in history.read_text().splitlines()]
        assert entries[0]["data"]["text"] == "秘密\nline\ud800"
        assert entries[0]["event"] == "user_input"
        assert checkpoint.read_bytes() == original
        before = history.read_bytes()
        session.commit_messages(tool_messages())
        assert history.read_bytes() == before
        history.write_bytes(before + b'{"truncated":')
    with store.resume("events") as resumed:
        assert resumed.messages == tool_messages()
        resumed.record("continued", sequence=999)
        assert history.read_bytes().startswith(before + b'{"truncated":\n')
        assert json.loads(history.read_text().splitlines()[-1])["event"] == "continued"
    history.unlink()
    with store.resume("events") as resumed:
        assert resumed.messages == tool_messages()
        resumed.record("new_log")
    assert len(history.read_text().splitlines()) == 1


@pytest.mark.parametrize("failure", ["replace", "fsync", "mkstemp"])
def test_atomic_failure_preserves_disk_and_memory(
    store: SessionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    with store.create(tmp_path, INITIAL, "atomic") as session:
        checkpoint = session.path / "checkpoint.json"
        before = checkpoint.read_bytes()
        timestamp = session.updated_at
        session.record("before_failure")

        def fail(*args: Any, **kwargs: Any) -> None:
            raise OSError("injected failure")

        owner = storage.tempfile if failure == "mkstemp" else storage.os
        with monkeypatch.context() as patch:
            patch.setattr(owner, failure, fail)
            with pytest.raises(SessionError, match="injected failure"):
                session.commit_messages(tool_messages())
        assert checkpoint.read_bytes() == before
        assert session.messages == INITIAL
        assert session.updated_at == timestamp
        assert not list(session.path.glob(".checkpoint-*.tmp"))
        session.record("after_failure")
    with store.resume("atomic") as resumed:
        assert resumed.messages == INITIAL
    assert len((session.path / "history.jsonl").read_text().splitlines()) == 2


def test_initial_save_failure_not_resumable(
    store: SessionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("initial save failed")

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail)
        with pytest.raises(SessionError):
            store.create(tmp_path, INITIAL, "initial")
    with pytest.raises(SessionError):
        store.resume("initial")
    assert not (store.root / hashlib.sha256(b"initial").hexdigest() / "checkpoint.json").exists()


def invalid_sequences() -> list[Any]:
    valid = tool_messages()
    return [
        [],
        {},
        ["bad"],
        [{"role": "unknown", "content": "x"}],
        [{"role": [], "content": "x"}],
        [{"role": "user", "content": 7}],
        INITIAL + [valid[2]],  # calls without results
        INITIAL + [valid[3]],  # orphan result
        INITIAL + [valid[2], valid[3], valid[-1]],  # missing one result
        INITIAL + [valid[2], valid[3], valid[3], valid[4]],  # duplicate result
        INITIAL + [{**valid[2], "tool_calls": [valid[2]["tool_calls"][0]] * 2}],
        INITIAL + [{"role": "user", "tool_calls": [], "content": "x"}],
        INITIAL + [{"role": "assistant", "function_call": {}, "content": None}],
        INITIAL + [{"role": "user", "tool_call_id": "a", "content": "x"}],
        INITIAL + [valid[2], {**valid[3], "name": "wrong"}, valid[4]],
        INITIAL + [{"role": "assistant", "tool_calls": [None]}],
        INITIAL + [{"role": "assistant", "tool_calls": [{"id": "a"}]}],
    ]


@pytest.mark.parametrize("messages", invalid_sequences())
def test_invalid_message_commit_preserves_checkpoint(
    store: SessionStore,
    tmp_path: Path,
    messages: Any,
) -> None:
    with store.create(tmp_path, INITIAL, "invalid") as session:
        checkpoint = session.path / "checkpoint.json"
        before = checkpoint.read_bytes()
        with pytest.raises(SessionError):
            session.commit_messages(messages)
        assert checkpoint.read_bytes() == before
        assert session.messages == INITIAL


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"session_id": "other"},
        {"messages": []},
        {"messages": tool_messages()[:3]},
        {"workspace": "relative"},
        {"workspace": 42},
        {"created_at": "bad"},
        {"updated_at": "2026-01-01"},
        {"extra": True},
    ],
)
def test_corrupt_checkpoint_rejected_and_lock_released(
    store: SessionStore,
    tmp_path: Path,
    mutation: dict[str, Any],
) -> None:
    with store.create(tmp_path, INITIAL, "corrupt") as session:
        path = session.path / "checkpoint.json"
        original = path.read_bytes()
    data = json.loads(original)
    data.update(mutation)
    path.write_text(json.dumps(data))
    with pytest.raises(SessionError):
        store.resume("corrupt")
    path.write_bytes(original)
    with store.resume("corrupt") as resumed:
        assert resumed.messages == INITIAL


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b"\xff", b'{"messages": NaN}'])
def test_invalid_json_rejected(store: SessionStore, tmp_path: Path, raw: bytes) -> None:
    with store.create(tmp_path, INITIAL, "broken") as session:
        path = session.path / "checkpoint.json"
    path.write_bytes(raw)
    with pytest.raises(SessionError):
        store.resume("broken")


def test_missing_workspace_refuses_resume(store: SessionStore, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with store.create(workspace, INITIAL, "workspace"):
        pass
    workspace.rmdir()
    with pytest.raises(SessionError, match="directory does not exist"):
        store.resume("workspace")


def _lock_child(root: str, connection: Any) -> None:
    try:
        session = SessionStore(root).resume("locked")
    except SessionError as exc:
        connection.send(("error", str(exc)))
    else:
        connection.send(("locked", session.messages))
        connection.recv()
        # Intentionally don't close; OS must release lock at process exit.
        os._exit(0)
    finally:
        connection.close()


def test_cross_process_nonblocking_lock_and_exit_release(
    store: SessionStore,
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    with store.create(tmp_path, INITIAL, "locked") as session:
        parent, child = context.Pipe()
        process = context.Process(target=_lock_child, args=(str(store.root), child))
        process.start()
        assert parent.poll(15), "child failed to answer"
        result = parent.recv()
        process.join(15)
        assert not process.is_alive()
        assert result[0] == "error" and "locked" in result[1]
        assert session.messages == INITIAL
        parent.close()
        child.close()
    parent, child = context.Pipe()
    process = context.Process(target=_lock_child, args=(str(store.root), child))
    process.start()
    try:
        assert parent.poll(15)
        assert parent.recv() == ("locked", INITIAL)
        with pytest.raises(SessionError, match="locked"):
            store.resume("locked")
        parent.send("exit")
        process.join(15)
        assert process.exitcode == 0
        with store.resume("locked") as resumed:
            assert resumed.messages == INITIAL
    finally:
        if process.is_alive():
            process.terminate()
            process.join(15)
        parent.close()
        child.close()


def test_closed_session_rejects_writes_and_close_is_idempotent(
    store: SessionStore,
    tmp_path: Path,
) -> None:
    with store.create(tmp_path, INITIAL, "closed") as session:
        pass
    session.close()
    with pytest.raises(SessionError, match="closed"):
        session.commit_messages(INITIAL)
    with pytest.raises(SessionError, match="closed"):
        session.record("event")
    with store.resume("closed"):
        pass


def test_checkpoint_size_limit(store: SessionStore, tmp_path: Path, monkeypatch: Any) -> None:
    with store.create(tmp_path, INITIAL, "size") as session:
        path = session.path / "checkpoint.json"
        before = path.read_bytes()
        monkeypatch.setattr(storage, "MAX_CHECKPOINT_BYTES", 10)
        with pytest.raises(SessionError, match="size limit"):
            session.commit_messages(INITIAL)
        assert path.read_bytes() == before
    with pytest.raises(SessionError, match="size limit"):
        store.resume("size")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions; Windows inherits profile ACLs")
def test_private_posix_modes(store: SessionStore, tmp_path: Path) -> None:
    with store.create(tmp_path, INITIAL, "private") as session:
        session.record("event")
        for directory in (store.root, session.path):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        for name in ("checkpoint.json", "writer.lock", "history.jsonl"):
            assert stat.S_IMODE((session.path / name).stat().st_mode) == 0o600


def _symlink(target: Path, link: Path, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


def test_linked_root_rejected_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "linked"
    _symlink(target, root, True)
    with pytest.raises(SessionError, match="links"):
        SessionStore(root / "sessions").create(tmp_path, INITIAL, "x")
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("name", ["checkpoint.json", "history.jsonl", "writer.lock"])
def test_linked_files_rejected(store: SessionStore, tmp_path: Path, name: str) -> None:
    with store.create(tmp_path, INITIAL, "link") as session:
        path = session.path / name
    target = tmp_path / "target"
    target.write_text("untouched")
    if path.exists():
        path.unlink()
    _symlink(target, path)
    if name == "history.jsonl":
        with store.resume("link") as resumed:
            with pytest.raises(SessionError, match="links"):
                resumed.record("event")
    else:
        with pytest.raises(SessionError, match="links"):
            store.resume("link")
    assert target.read_text() == "untouched"


def test_record_invalid_data_preserves_log(store: SessionStore, tmp_path: Path) -> None:
    with store.create(tmp_path, INITIAL, "event") as session:
        session.record("good")
        log = session.path / "history.jsonl"
        before = log.read_bytes()
        for event, data in [("", {}), (1, {}), ("bad", {"value": float("nan")})]:
            with pytest.raises(SessionError):
                session.record(event, **data)
            assert log.read_bytes() == before


@pytest.mark.parametrize(
    "platform,variable,suffix",
    [
        ("win32", "LOCALAPPDATA", ("orz-agent", "sessions")),
        ("linux", "XDG_DATA_HOME", ("orz-agent", "sessions")),
    ],
)
def test_platform_data_root(
    monkeypatch: Any, tmp_path: Path, platform: str, variable: str, suffix: tuple[str, ...]
) -> None:
    monkeypatch.setattr(storage.sys, "platform", platform)
    monkeypatch.setenv(variable, str(tmp_path))
    assert storage.default_session_root() == tmp_path.joinpath(*suffix)


def test_surrogate_checkpoint_roundtrip(store: SessionStore, tmp_path: Path) -> None:
    messages = INITIAL + [{"role": "user", "content": "raw\ud800"}]
    with store.create(tmp_path, messages, "unicode"):
        pass
    with store.resume("unicode") as resumed:
        assert resumed.messages == messages


def test_history_failure_does_not_modify_checkpoint(store: SessionStore, tmp_path: Path) -> None:
    with store.create(tmp_path, INITIAL, "history-failure") as session:
        checkpoint = session.path / "checkpoint.json"
        before = checkpoint.read_bytes()
        (session.path / "history.jsonl").mkdir()
        with pytest.raises(SessionError):
            session.record("event")
        assert checkpoint.read_bytes() == before
        session.commit_messages(tool_messages())
    with store.resume("history-failure") as resumed:
        assert resumed.messages == tool_messages()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction reparse points")
def test_windows_junction_rejected(tmp_path: Path) -> None:
    import _winapi

    target = tmp_path / "junction-target"
    target.mkdir()
    link = tmp_path / "junction"
    _winapi.CreateJunction(str(target), str(link))
    try:
        with pytest.raises(SessionError, match="reparse"):
            SessionStore(link / "sessions").create(tmp_path, INITIAL, "junction")
        assert list(target.iterdir()) == []
    finally:
        link.rmdir()


@pytest.mark.parametrize("operation", ["create", "resume"])
@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, RuntimeError])
def test_session_open_failure_releases_lock(
    store: SessionStore,
    tmp_path: Path,
    monkeypatch: Any,
    operation: str,
    exception: type[BaseException],
) -> None:
    if operation == "resume":
        with store.create(tmp_path, INITIAL, "interrupted") as original:
            before = (original.path / "checkpoint.json").read_bytes()

    def interrupt(*args: Any, **kwargs: Any) -> None:
        raise exception("injected interruption")

    with monkeypatch.context() as patch:
        if operation == "create":
            patch.setattr(storage.Session, "_save", interrupt)
        else:
            patch.setattr(store, "_read", interrupt)
        with pytest.raises(exception, match="injected interruption"):
            if operation == "create":
                store.create(tmp_path, INITIAL, "interrupted")
            else:
                store.resume("interrupted")
    # Even a failed initial create leaves no live lock behind its fail-closed directory.
    with storage.Session(store._path("interrupted"), {"session_id": "interrupted"}):
        pass
    if operation == "resume":
        with store.resume("interrupted") as resumed:
            assert resumed.messages == INITIAL
            assert (resumed.path / "checkpoint.json").read_bytes() == before


def test_lock_acquisition_interrupt_closes_descriptor(
    store: SessionStore,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    with store.create(tmp_path, INITIAL, "acquisition"):
        pass
    descriptors = []
    original_open = storage._open

    def track_open(*args: Any, **kwargs: Any) -> int:
        fd = original_open(*args, **kwargs)
        descriptors.append(fd)
        return fd

    def interrupt(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt("lock interrupted")

    if os.name == "nt":
        import msvcrt

        lock_module, lock_function = msvcrt, "locking"
    else:
        import fcntl

        lock_module, lock_function = fcntl, "flock"
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_open", track_open)
        patch.setattr(lock_module, lock_function, interrupt)
        with pytest.raises(KeyboardInterrupt, match="lock interrupted"):
            store.resume("acquisition")
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    with store.resume("acquisition"):
        pass


def test_atomic_save_interrupt_preserves_checkpoint_and_cleans_temporary(
    store: SessionStore,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    with store.create(tmp_path, INITIAL, "save-interrupted") as session:
        checkpoint = session.path / "checkpoint.json"
        before = checkpoint.read_bytes()

        def interrupt(*args: Any, **kwargs: Any) -> None:
            raise KeyboardInterrupt("save interrupted")

        with monkeypatch.context() as patch:
            patch.setattr(storage.os, "replace", interrupt)
            with pytest.raises(KeyboardInterrupt, match="save interrupted"):
                session.commit_messages(tool_messages())
        assert checkpoint.read_bytes() == before
        assert session.messages == INITIAL
        assert not list(session.path.glob(".checkpoint-*.tmp"))
    with store.resume("save-interrupted") as resumed:
        assert resumed.messages == INITIAL


@pytest.mark.parametrize(
    "assistant",
    [
        {"role": "assistant", "content": None},
        {"role": "assistant", "content": None, "refusal": "I cannot help with that."},
        {"role": "assistant", "content": None, "refusal": None},
        {"role": "assistant", "refusal": "No."},
    ],
)
def test_assistant_null_content_roundtrip(
    store: SessionStore, tmp_path: Path, assistant: dict[str, Any]
) -> None:
    messages = INITIAL + [{"role": "user", "content": "hello"}, assistant]
    with store.create(tmp_path, INITIAL, "null-content") as session:
        session.commit_messages(messages)
    with store.resume("null-content") as resumed:
        assert resumed.messages == messages


@pytest.mark.parametrize("refusal", [False, 7, [], {}])
def test_invalid_refusal_preserves_checkpoint(
    store: SessionStore, tmp_path: Path, refusal: Any
) -> None:
    with store.create(tmp_path, INITIAL, "bad-refusal") as session:
        checkpoint = session.path / "checkpoint.json"
        before = checkpoint.read_bytes()
        with pytest.raises(SessionError, match="refusal"):
            session.commit_messages(
                INITIAL + [{"role": "assistant", "content": None, "refusal": refusal}]
            )
        assert session.messages == INITIAL
        assert checkpoint.read_bytes() == before


@pytest.mark.parametrize("role", ["system", "developer", "user", "tool"])
def test_null_content_still_rejected_for_other_roles(
    store: SessionStore, tmp_path: Path, role: str
) -> None:
    messages = INITIAL + [{"role": role, "content": None, "refusal": "not an assistant"}]
    if role == "tool":
        valid = tool_messages()
        messages = valid[:3] + [{**valid[3], "content": None}, valid[4]]
    with store.create(tmp_path, INITIAL, "null-invalid") as session:
        checkpoint = session.path / "checkpoint.json"
        before = checkpoint.read_bytes()
        with pytest.raises(SessionError, match="content"):
            session.commit_messages(messages)
        assert session.messages == INITIAL
        assert checkpoint.read_bytes() == before


def test_duplicate_pending_call_id_rejected_with_complete_results(
    store: SessionStore, tmp_path: Path
) -> None:
    call = tool_messages()[2]["tool_calls"][0]
    # One result satisfies every distinct ID; only the duplicate-call guard rejects this.
    messages = INITIAL + [
        {"role": "assistant", "content": None, "tool_calls": [call, copy.deepcopy(call)]},
        {"role": "tool", "tool_call_id": "a", "name": "read", "content": "one"},
        {"role": "assistant", "content": "done"},
    ]
    with store.create(tmp_path, INITIAL, "duplicate-call") as session:
        before = (session.path / "checkpoint.json").read_bytes()
        with pytest.raises(SessionError, match="invalid or duplicate tool call"):
            session.commit_messages(messages)
        assert session.messages == INITIAL
        assert (session.path / "checkpoint.json").read_bytes() == before


def test_completed_call_id_can_be_reused_in_later_messages(
    store: SessionStore, tmp_path: Path
) -> None:
    messages = tool_messages() + tool_messages()[1:]
    with store.create(tmp_path, messages, "reused-call"):
        pass
    with store.resume("reused-call") as resumed:
        assert resumed.messages == messages


def test_storage_root_expands_home_without_creating_literal_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(tmp_path)
    store = SessionStore("~/sessions")
    assert store.root == home / "sessions"
    with store.create(tmp_path, INITIAL, "home"):
        pass
    assert store.root.is_dir()
    assert not (tmp_path / "~").exists()


def test_storage_root_file_reports_clear_error_without_modification(tmp_path: Path) -> None:
    root = tmp_path / "not-a-directory"
    root.write_bytes(b"unchanged")
    with pytest.raises(SessionError, match="not a directory") as error:
        SessionStore(root).create(tmp_path, INITIAL, "file-root")
    assert str(root) in str(error.value)
    assert root.read_bytes() == b"unchanged"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits replacing an open lock inode")
def test_replaced_lock_inode_rejects_writes_without_changing_checkpoint(
    store: SessionStore, tmp_path: Path
) -> None:
    with store.create(tmp_path, INITIAL, "replaced-lock") as session:
        checkpoint = session.path / "checkpoint.json"
        before = checkpoint.read_bytes()
        lock = session.path / "writer.lock"
        held_inode = lock.stat().st_ino
        replacement = session.path / "replacement.lock"
        replacement.write_bytes(b"replacement")
        assert replacement.stat().st_ino != held_inode
        # Exact inspected paths are both inside this test's session directory.
        os.replace(replacement, lock)
        with pytest.raises(SessionError, match="writer lock was replaced"):
            session.commit_messages(tool_messages())
        with pytest.raises(SessionError, match="writer lock was replaced"):
            session.record("must-not-be-written")
        assert checkpoint.read_bytes() == before
        assert session.messages == INITIAL
        assert not (session.path / "history.jsonl").exists()
