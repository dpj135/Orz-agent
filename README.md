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

## Current scope

- One agent and one serial work loop.
- OpenAI-compatible Chat Completions API.
- Tool-call JSON parsing, bounded steps, workspace path checks, and bounded output.
- No multi-agent collaboration yet; that is a later iteration.

## Workspace instructions and skills

At the start of **each task**, Python reads the workspace root `AGENTS.md` and
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
share `BaseTool`, `ToolExecutionContext`, and public capabilities in `tool_context.py`
(policy, path checks, approval, limits, output helpers). Add a new tool module and
tests without modifying the work loop or a central dispatch branch. Automatic
discovery does not grant write/process permissions. The original `tools.schemas`
and `tools.execute` helpers remain compatibility wrappers; new code should use
`ToolRegistry` directly.

This refactor retains existing file/process behavior and its limitations. It adds
runtime checks for declared argument types and positive byte limits; numeric
strings and booleans are not accepted as integer limits. It does not introduce
OS isolation, bounded-memory reads, subprocess environment sanitization, or
process-tree cancellation.

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
