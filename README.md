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
