from __future__ import annotations

import argparse
import os
from pathlib import Path

from .loop import run_turn
from .model import ModelClient
from .tools import Policy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A small single-agent CLI")
    parser.add_argument("prompt", nargs="?", help="one-shot prompt; omit for REPL")
    parser.add_argument(
        "--base-url", default=os.getenv("AGENT_BASE_URL", "https://api.openai.com/v1")
    )
    parser.add_argument("--model", default=os.getenv("AGENT_MODEL", "gpt-4o-mini"))
    parser.add_argument("--api-key", default=None, help="prefer AGENT_API_KEY environment variable")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--allow-command", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    key = args.api_key or os.getenv("AGENT_API_KEY")
    if not key:
        print("Missing API key: set AGENT_API_KEY or pass --api-key", flush=True)
        return 2
    workspace = args.workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    client = ModelClient(key, args.base_url, args.model)
    policy = Policy(
        workspace=workspace, allow_write=args.allow_write, allow_command=args.allow_command
    )

    def confirm(prompt: str) -> bool:
        try:
            return input(prompt).strip().lower() in {"y", "yes"}
        except EOFError:
            return False

    try:
        if args.prompt:
            run_turn(args.prompt, client, policy, confirm, args.max_steps)
            return 0
        print(f"workspace: {workspace}")
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
                run_turn(text, client, policy, confirm, args.max_steps)
            except KeyboardInterrupt:
                print("[cancelled]")
            except Exception as exc:
                print(f"error: {exc}")
    except Exception as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
