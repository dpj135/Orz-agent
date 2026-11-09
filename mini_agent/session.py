"""Durable checkpoints and independent interaction logs."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII)
_FIELDS = {"schema_version", "session_id", "messages", "workspace", "created_at", "updated_at"}


class SessionError(RuntimeError):
    """Invalid, unavailable, unsafe, or unwritable session storage."""


def validate_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or _ID_PATTERN.fullmatch(session_id) is None:
        raise SessionError("session ID must contain 1-64 ASCII letters, digits, '_' or '-'")
    return session_id


def default_session_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "orz-agent" / "sessions"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _check_path(path: Path) -> None:
    # Defense in depth, not an OS sandbox against hostile directory-renaming races.
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SessionError(f"links and reparse points are not allowed: {part}")


def _directory(path: Path, *, create: bool = False) -> None:
    _check_path(path)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_path(path)
    if not path.is_dir():
        raise SessionError(f"directory does not exist: {path}")


def _open(path: Path, flags: int) -> int:
    _check_path(path)
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise SessionError(f"not a regular file: {path}")
    except FileNotFoundError:
        pass
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        _check_path(path)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SessionError(f"not a regular file: {path}")
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError, RecursionError) as exc:
        raise SessionError("session data must be finite JSON values") from exc


def _validate_messages(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list) or not messages:
        raise SessionError("messages must be a nonempty list")
    pending: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict):
            raise SessionError("each message must be an object")
        role = message.get("role")
        if not isinstance(role, str) or role not in {
            "system",
            "developer",
            "user",
            "assistant",
            "tool",
        }:
            raise SessionError("invalid message role")
        content = message.get("content")
        if not isinstance(content, (str, list)) and not (
            content is None and role == "assistant" and message.get("tool_calls")
        ):
            raise SessionError("invalid or missing message content")
        if pending and role != "tool":
            raise SessionError("tool calls must all have results before another message")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                raise SessionError("orphan or duplicate tool result")
            name = message.get("name")
            if name is not None and name != pending[call_id]:
                raise SessionError("tool result name does not match call")
            if not isinstance(content, str):
                raise SessionError("tool result content must be text")
            del pending[call_id]
        calls = message.get("tool_calls")
        if calls is not None:
            if role != "assistant" or not isinstance(calls, list):
                raise SessionError("tool_calls must be an assistant list")
            for call in calls:
                if not isinstance(call, dict):
                    raise SessionError("invalid tool call")
                call_id = call.get("id")
                function = call.get("function")
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or call_id in pending
                    or call.get("type") != "function"
                    or not isinstance(function, dict)
                    or not isinstance(function.get("name"), str)
                    or not function["name"]
                    or not isinstance(function.get("arguments"), str)
                ):
                    raise SessionError("invalid or duplicate tool call")
                pending[call_id] = function["name"]
        if role != "tool" and "tool_call_id" in message:
            raise SessionError("tool_call_id is only valid on tool results")
        if "function_call" in message:
            raise SessionError("legacy function calls are not supported")
    if pending:
        raise SessionError("incomplete tool call sequence")
    return _json_clone(messages)


def _validate_checkpoint(data: Any, session_id: str) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != _FIELDS:
        raise SessionError("invalid checkpoint fields")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise SessionError("unsupported checkpoint schema version")
    if data["session_id"] != session_id:
        raise SessionError("checkpoint session ID mismatch")
    data["messages"] = _validate_messages(data["messages"])
    for field in ("created_at", "updated_at"):
        try:
            timestamp = datetime.fromisoformat(data[field])
            if timestamp.tzinfo is None:
                raise ValueError("timezone missing")
        except (TypeError, ValueError) as exc:
            raise SessionError(f"invalid checkpoint {field}") from exc
    if not isinstance(data["workspace"], str) or not Path(data["workspace"]).is_absolute():
        raise SessionError("invalid checkpoint workspace")
    _directory(Path(data["workspace"]))
    return data


class SessionStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = _absolute(default_session_root() if root is None else root)

    def _prepare(self) -> None:
        _directory(self.root, create=True)
        # Windows chmod cannot express ACLs; inherit user-profile directory ACLs.
        if os.name != "nt":
            self.root.chmod(0o700)

    def _path(self, session_id: str) -> Path:
        identity = validate_session_id(session_id).encode("ascii")
        return self.root / hashlib.sha256(identity).hexdigest()

    def create(
        self,
        workspace: str | Path,
        messages: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> Session:
        session_id = str(uuid.uuid4()) if session_id is None else validate_session_id(session_id)
        session: Session | None = None
        try:
            workspace_path = _absolute(workspace)
            _directory(workspace_path)
            validated = _validate_messages(messages)
            self._prepare()
            path = self._path(session_id)
            _check_path(path)
            try:
                path.mkdir(mode=0o700)
            except FileExistsError as exc:
                raise SessionError(f"session already exists: {session_id}") from exc
            timestamp = _now()
            data = {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "messages": validated,
                "workspace": str(workspace_path),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            session = Session(path, data)
            session._save(data)
            return session
        except BaseException as exc:
            if session is not None:
                session.close()
            if isinstance(exc, SessionError) or not isinstance(
                exc, (OSError, ValueError, TypeError)
            ):
                raise
            raise SessionError(f"cannot create session: {exc}") from exc

    def resume(self, session_id: str) -> Session:
        path = self._path(session_id)
        session: Session | None = None
        try:
            _directory(path)
            session = Session(path, {"session_id": session_id})
            session._data = self._read(path, session_id)
            return session
        except BaseException as exc:
            if session is not None:
                session.close()
            if isinstance(exc, SessionError) or not isinstance(
                exc, (OSError, ValueError, TypeError)
            ):
                raise
            raise SessionError(f"cannot resume session: {exc}") from exc

    @staticmethod
    def _read(path: Path, session_id: str) -> dict[str, Any]:
        with os.fdopen(_open(path / "checkpoint.json", os.O_RDONLY), "rb") as stream:
            raw = stream.read(MAX_CHECKPOINT_BYTES + 1)
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise SessionError("checkpoint exceeds size limit")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SessionError("corrupt checkpoint JSON") from exc
        return _validate_checkpoint(data, session_id)


class Session:
    """A single-writer session, held open for the entire interactive lifecycle."""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self._data = data
        self._lock_fd: int | None = None
        fd = _open(path / "writer.lock", os.O_RDWR | os.O_CREAT)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as exc:
            os.close(fd)
            if isinstance(exc, OSError):
                raise SessionError("session is locked by another writer") from exc
            raise
        self._lock_fd = fd

    @property
    def session_id(self) -> str:
        return self._data["session_id"]

    @property
    def workspace(self) -> Path:
        return Path(self._data["workspace"])

    @property
    def messages(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._data["messages"])

    @property
    def created_at(self) -> str:
        return self._data["created_at"]

    @property
    def updated_at(self) -> str:
        return self._data["updated_at"]

    def _ensure_open(self) -> None:
        if self._lock_fd is None:
            raise SessionError("session is closed")
        _directory(self.path)
        _check_path(self.path / "writer.lock")
        current = (self.path / "writer.lock").stat()
        held = os.fstat(self._lock_fd)
        if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise SessionError("session writer lock was replaced")

    def _save(self, data: dict[str, Any]) -> None:
        self._ensure_open()
        target = self.path / "checkpoint.json"
        _check_path(target)
        payload = json.dumps(data, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(payload) > MAX_CHECKPOINT_BYTES:
            raise SessionError("checkpoint exceeds size limit")
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", suffix=".tmp", dir=self.path)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _check_path(target)
            _directory(self.path)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def commit_messages(self, messages: list[dict[str, Any]]) -> None:
        try:
            self._ensure_open()
            data = {**self._data, "messages": _validate_messages(messages), "updated_at": _now()}
            self._save(data)
            self._data = data
        except (OSError, ValueError, TypeError) as exc:
            raise SessionError(f"cannot save checkpoint: {exc}") from exc

    def record(self, event: str, **data: Any) -> None:
        """Flush an appended event; a truncated prior tail is never parsed or repaired."""
        if not isinstance(event, str) or not event:
            raise SessionError("event must be a nonempty string")
        try:
            self._ensure_open()
            entry = {"event": event, "timestamp": _now(), "data": _json_clone(data)}
            line = json.dumps(entry, ensure_ascii=True, allow_nan=False) + "\n"
            path = self.path / "history.jsonl"
            if path.exists() and path.stat().st_size:
                with os.fdopen(_open(path, os.O_RDONLY), "rb") as existing:
                    existing.seek(-1, os.SEEK_END)
                    needs_separator = existing.read(1) != b"\n"
            else:
                needs_separator = False
            fd = _open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
            with os.fdopen(fd, "a", encoding="utf-8", newline="\n") as stream:
                if needs_separator:
                    stream.write("\n")
                stream.write(line)
                stream.flush()
        except (OSError, ValueError, TypeError) as exc:
            raise SessionError(f"cannot append history: {exc}") from exc

    def close(self) -> None:
        fd = self._lock_fd
        if fd is None:
            return
        self._lock_fd = None
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> Session:
        self._ensure_open()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
