# Orz-agent

A small single-agent Python CLI for `dpj135/Orz-agent`.

## Run

Python 3.11+ is required. Install dependencies from the repository root:

```sh
python -m pip install -e ".[dev]"
```

```powershell
$env:AGENT_API_KEY = "your-key"
python -m mini_agent --base-url https://api.openai.com/v1 --model gpt-4o-mini --workspace .
```

One-shot mode:

```powershell
python -m mini_agent "Read README.md and summarize it"
```

The default tools are `list_directory` and `read_file`. Writing requires `--allow-write` and interactive confirmation. Process execution requires `--allow-command`, interactive confirmation, and an allowlisted executable (`python` or `git`); it never uses a shell.

This is an MVP, not a strong OS sandbox. Keep the workspace and API endpoint trusted, and do not use it to execute untrusted code.

## Sessions and local history

```sh
# New random UUID; or create an explicitly named, unused session
agent --session-id my-task "Inspect the project"
# Resume the saved messages and workspace; missing IDs are errors
agent --resume my-task "Continue"
# Explicit compatibility option for an endpoint rejecting prompt_cache_key
agent --resume my-task --no-prompt-cache "Continue"
# Store sessions in a chosen root (use the same root when resuming)
agent --session-dir ./local-sessions --session-id example "Hello"
```

Without identity flags, every invocation creates a new UUID session. REPL turns
share the same session. `--session-id` and `--resume` are mutually exclusive;
creating an existing ID never overwrites or resumes it. An initial storage failure
may leave a reserved but invalid session directory; it is not silently reused.
Inspect/remove such incomplete data manually only when no writer is running.
IDs must match
`[A-Za-z0-9_-]{1,64}` (ASCII). There is no `AGENT_SESSION_ID` override. The
64-character limit also bounds the cache key; IDs are never silently truncated.

Default roots are `%LOCALAPPDATA%/orz-agent/sessions` on Windows,
`${XDG_DATA_HOME:-~/.local/share}/orz-agent/sessions` on Linux, and
`~/Library/Application Support/orz-agent/sessions` on macOS. Each ID maps to a
SHA-256 hex digest directory derived from the ASCII ID, avoiding path traversal,
platform filename collisions and unnecessarily long filenames.
Each session contains a checkpoint, an independent append-only JSONL history,
and a writer lock. A second writer is rejected; OS locks release on process exit.
Do not delete/replace lock files while sessions are running. Use local filesystems
with reliable file locking and atomic replacement semantics.

The checkpoint stores only format version, session ID, messages, workspace and
creation/update times. Checkpoints are capped at 32 MiB; exceeding the limit fails
explicitly without truncation. New identity and initial context are saved before the first
request; complete turns atomically replace the checkpoint. Failed/interrupted turns
do not replace the last successful context. Resume loads those messages without
reinjecting current workspace instructions. It uses the saved workspace (which must
still exist), and rejects a conflicting explicit `--workspace`. Current model,
credentials, tool implementations and permissions are used, not historical runtime
snapshots. Keep the current configuration compatible with the saved conversation.
Skill bodies are still loaded on demand from the current workspace.

History records user inputs, parsed LLM responses, tool starts/results/errors and
turn outcomes as events occur. It is independent of messages: it is not replayed,
not used to repair context, and will not be erased by future context compression.
There is no checkpoint-to-log event sequence binding. `turn_end` describes the
interaction outcome, not durable checkpoint success. A later save failure records
`checkpoint_error` and reports that the answer was generated but context was not
saved; it does not append a contradictory failed interaction outcome.

Before executing any tool in a response, compatible calls are normalized in a
copy: absent types become `function`, absent IDs receive nonconflicting IDs, and
object arguments become JSON text. Duplicate IDs or unsupported structures reject
the entire batch before execution. History retains the original parsed response.
Assistant refusals and empty content remain resumable; the terminal displays text
parts or refusal text without replacing the saved message structure.

`--session-dir` expands `~` but does not resolve away linked path components.
Storage paths and their ancestors must not contain symlinks or reparse points,
including system aliases such as macOS `/tmp`; use their physical paths instead.

A crash can leave an
unfinished event or a partial final log line; logging is not a transaction or an
exactly-once execution guarantee. A successful tool can have changed files even
when its turn was never checkpointed. Resume neither rolls back such effects nor
automatically reruns tools. Corrupt/unsupported checkpoints and unmatched tool
calls are rejected rather than guessed or repaired. Logs/trace UI, compression
and full audit facilities are separate future work.

Session IDs are also cache keys (`prompt_cache_key`) in this Chat Completions
adapter, enabled by default for the OpenAI protocol. They remain stable across turns,
tool loops and restarts. Custom gateway support has **not** been verified; use
`--no-prompt-cache` for incompatible endpoints. A rejection fails once with actionable
guidance: no silent field removal/retry or endpoint switch. The key is a routing
hint, not server-side history or context isolation. Full required messages are
still sent every time. Stable prefixes can help caching but do not guarantee a hit
or reduced cost. Explicit IDs are sent to the target API: never use paths, usernames,
keys or other sensitive information as IDs.

Authentication configuration/headers and environment snapshots are not written to
sessions. User input, model output and tool results can themselves contain secrets
or code; treat both checkpoint and history as sensitive local files. No extra log
upload is performed. Subsequent model requests still send the required context to
the configured endpoint. Restrict access to the storage root using OS permissions;
POSIX files/directories are created private, while Windows inherits user-directory
ACLs. Application-level path checks are not race-free OS isolation.

## Current scope

- One agent and one serial work loop.
- OpenAI-compatible Chat Completions API.
- Tool-call JSON parsing, bounded steps, workspace path checks, and bounded output.
- No multi-agent collaboration yet; that is a later iteration.

## Unicode handling

Terminal input can carry undecodable bytes that Python keeps as unpaired
surrogates (U+D800–U+DFFF). JSON forbids them, so a strict gateway rejects the
whole request with 400. The prompt is sanitized once when it enters the
conversation: each unpaired surrogate becomes U+FFFD and one warning records the
replacement count, never the text. Clean prompts stay silent. Tool results and
assistant replies are trusted and passed through unchanged.

## Workspace instructions and skills

At the start of **each new session**, Python reads the workspace root `AGENTS.md` and
scans `.agents/skills/*/SKILL.md`. It adds workspace guidance and a sorted catalog
of skill names/descriptions to user-role context before the actual user request.
Repository content is not promoted into the system prompt.

A skill starts with exactly two YAML front-matter fields:

```markdown
---
name: example
description: Use when performing example tasks.
---
# Workflow
Read the relevant files, make the change, and verify the result.
```

Only name/description summaries appear initially. The model selects a matching
skill and calls `skill` with `{"name":"example"}`. Python validates the registered
name and returns the full body plus its workspace-relative `base_directory` as a
tool result. Referenced resources are read only as needed; scripts are never
executed automatically. This selection is model-driven guidance, not enforcement.

Limits: 32 directory entries under the skill root, 32 KiB per instruction file,
64 characters per kebab-case name, 500 characters per description. Duplicate names
are all rejected. Malformed/unreadable entries produce warnings without exposing
their content. Linked/reparse-point paths are rejected, including on load; these
checks are not race-free OS isolation. Loading re-reads current content, but metadata
changes require a new task. There are no directory watchers, nested instruction
chains, global skill sources, slash commands, or automatic resource loading.
Only workspaces you trust should be used. Skills never enable write/command
permissions, and the pre-existing process tool remains unsuitable for untrusted code.

Run offline tests (no API key required):

```sh
python -m pytest
```

## Tool architecture

Built-in tools live in `mini_agent/tools/`, one concrete `BaseTool` subclass per
module. Implement `name`, `schema()` (one OpenAI tool definition), and `execute()`;
`validate()` provides shared field/type/minimum checks for the built-in schema
subset and can be overridden for tool-specific requirements. It is not a general
JSON Schema engine.

`ToolRegistry.discover()` imports only the installed built-in tools package in
sorted order, rejects duplicate names/schema mismatches, and retains tool instances.
The work loop creates one registry per task and uses it for schemas and dispatch.
It never scans the user's workspace for Python plugins. Importing trusted built-in
modules executes their top-level code: discovery is not a security sandbox.

Concrete tools must not import or invoke each other or retain task state. They
share `BaseTool`, `ToolExecutionContext`, and public capabilities in `tool_execution_context.py`
(policy, path checks, approval, limits, output helpers). Add a new tool module and
tests without modifying the work loop or a central dispatch branch. Automatic
discovery does not grant write/process permissions. The original `tools.schemas`
and `tools.execute` helpers remain compatibility wrappers; new code should use
`ToolRegistry` directly.

Existing `read_file` and `run_command` retain their original limitations, including
post-read truncation and inherited command environments. They are not OS isolation.

## Developer tools

All four tools are independent modules under `mini_agent/tools/` and are discovered
automatically without changing the work loop:

| Tool | Example arguments | Behavior |
| --- | --- | --- |
| `glob_files` | `{"path":".","pattern":"*.py"}` | Find files by `PurePath.match` patterns. |
| `search_text` | `{"path":"mini_agent","pattern":"class "}` | Case-sensitive literal search, not regex; return paths, one-based lines and text. |
| `edit_file` | `{"path":"note.txt","old_text":"before","new_text":"after"}` | Replace exactly one nonempty literal match; no guessing or automatic retries. |
| `run_pytest` | `{"paths":["tests"],"timeout":60}` | Run pytest through the current Python, with fixed flags and approved test paths. |

Search excludes dependency/cache directories, `.git`, and pip temporary directories.
It rejects linked/reparse paths. Both search tools accept optional `max_results`
(default/cap 100); traversal visits at most 10,000 entries and text search reads at
most 64 KiB per file plus one sentinel byte. Result payloads honor the policy byte
budget. `truncated` indicates omitted results, file tails, or unsafe/unreadable paths.
Binary/null-containing and invalid UTF-8 content is skipped. Glob semantics follow
`PurePath.match`: `*.py` matches Python basenames at any depth; `**` is not a full
recursive glob engine. Searches do not read ignored dependency trees explicitly.

Editing requires `--allow-write` plus confirmation showing the replacement. Reads
and resulting UTF-8 content must fit the policy byte budget (default 32 KiB).
Zero/multiple matches fail unchanged. Optional lowercase `expected_sha256` checks
original raw bytes; the file is rechecked after approval. Original line endings
are retained. These checks still have a final filesystem race and are not atomic
compare-and-swap or OS isolation.

Pytest requires installing pytest (`.[dev]` includes it), `--allow-command`, and
explicit confirmation of the full command. Only 1–20 existing workspace filesystem
paths are accepted: no option strings, node selectors, or arbitrary pytest flags.
Default timeout is 60 seconds, maximum 120. It returns exit code, output, summary,
truncation, timeout and cleanup status. Output is drained with bounded retention.
It disables pytest config-file options, automatic third-party plugins, and the
cache provider; conftest and test imports still execute code. Only a small OS/locale
environment allowlist is inherited, excluding API credentials and pytest/Python
configuration variables. This may require adapting tests that need custom settings.

Use only trusted tests: they can read files, access the network, modify the workspace
and launch child processes. Cleanup is best-effort (POSIX process groups; Windows
taskkill while the parent is alive), not complete descendant containment. Windows
children surviving an exited parent may remain; `cleanup_incomplete` reports an
output reader still held open. Do not treat approval or filtering as a sandbox.

## Development checks

Create a virtual environment with `python -m venv .venv`, then activate it:

- Windows PowerShell: `.venv\\Scripts\\Activate.ps1`
- Linux: `source .venv/bin/activate`

From the active environment, run:

```sh
python -m pip install -e ".[dev]"
python -m pre_commit install
python -m pre_commit run --all-files
```

Installing dependencies does **not** install the Git hook; each clone needs the
explicit install command. Local hooks use `language: system` and the active Python
environment, with checker versions pinned in the development extra. They do not
need Bash or download separate hook repositories. Package installation still needs
network access. Keep the environment active when committing.

Checks include Ruff lint/format, whitespace/EOF, merge conflicts, large files,
TOML/JSON/YAML syntax, and the complete offline pytest suite on every commit.
Python uses 4-space indentation, default double quotes, sorted imports, and a
100-character line width. Ruff E501 rejects overlong lines (with Ruff's built-in
exceptions such as single unbreakable words and qualifying URL lines); formatting
alone is not the line-length check. Split long strings manually when needed.
Real model evaluation is excluded: no API key is needed. Hooks may fix files;
review their changes, stage the intended files, and rerun until all checks pass.
Then commit with `git commit -s`. Do not bypass checks with `--no-verify` or `SKIP`.

GitHub Actions runs the same command on Windows/Linux and Python 3.11/3.12 for
pull requests and main pushes. Local hooks can be bypassed: repository admins
must separately protect main and require the four `checks (...)` status checks
before merge. Adding this workflow does not enable branch protection itself.

`AGENTS.md` defines repository-wide rules; `.agents/skills/python-development`,
`tool-security`, and `pre-commit-checks` provide task-specific workflows. Skill
guidance complements automated checks and cannot replace their execution.
