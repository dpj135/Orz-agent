from __future__ import annotations

import argparse
import os
from pathlib import Path

from .loop import initial_messages, run_turn
from .model import ModelClient
from .session import SessionError, SessionStore
from .tools import Policy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A small single-agent CLI")
    parser.add_argument("prompt", nargs="?", help="one-shot prompt; omit for REPL")
    parser.add_argument(
        "--base-url", default=os.getenv("AGENT_BASE_URL", "https://api.openai.com/v1")
    )
    parser.add_argument("--model", default=os.getenv("AGENT_MODEL", "gpt-4o-mini"))
    parser.add_argument("--api-key", default=None, help="prefer AGENT_API_KEY environment variable")
    parser.add_argument("--workspace", type=Path, default=None)
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--session-id", help="create a new session with an unused ID")
    identity.add_argument("--resume", metavar="ID", help="resume an existing session")
    parser.add_argument("--session-dir", type=Path, help="override the session storage root")
    parser.add_argument(
        "--no-prompt-cache",
        action="store_true",
        help="omit prompt_cache_key for endpoints that do not support it",
    )
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--allow-command", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_steps < 1:
        parser.error("--max-steps must be positive")
    key = args.api_key or os.getenv("AGENT_API_KEY")
    if not key:
        print("Missing API key: set AGENT_API_KEY or pass --api-key", flush=True)
        return 2

    def confirm(prompt: str) -> bool:
        try:
            return input(prompt).strip().lower() in {"y", "yes"}
        except EOFError:
            return False

    try:
        store = SessionStore(args.session_dir)
        if args.resume:
            session = store.resume(args.resume)
        else:
            workspace = (args.workspace or Path.cwd()).expanduser().resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            session = store.create(
                workspace,
                initial_messages(Policy(workspace)),
                session_id=args.session_id,
            )
        with session:
            workspace = session.workspace
            if args.resume and args.workspace is not None:
                if args.workspace.expanduser().resolve() != workspace:
                    raise SessionError("--workspace conflicts with the saved session workspace")
            print(f"session: {session.session_id} ({'resumed' if args.resume else 'new'})")
            print(f"workspace: {workspace}")
            if args.resume:
                print(
                    "Restored the last saved complete turn. Later tool side effects are not "
                    "rolled back; history is not replayed. Current model and tools are used."
                )
            client = ModelClient(
                key,
                args.base_url,
                args.model,
                prompt_cache_key=None if args.no_prompt_cache else session.session_id,
            )
            policy = Policy(
                workspace=workspace,
                allow_write=args.allow_write,
                allow_command=args.allow_command,
            )
            if args.prompt:
                run_turn(args.prompt, client, policy, confirm, args.max_steps, session=session)
                return 0
            print("Enter a prompt, or /exit to quit.")
            while True:
                try:
                    text = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if text == "/exit":
                    return 0
                if not text:
                    continue
                try:
                    run_turn(text, client, policy, confirm, args.max_steps, session=session)
                except KeyboardInterrupt:
                    print("[cancelled; last complete turn retained]")
                except SessionError:
                    raise
                except Exception as exc:
                    print(f"error: {exc}")
    except KeyboardInterrupt:
        print("[cancelled; last complete turn retained]")
        return 130
    except Exception as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
