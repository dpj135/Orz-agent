# Orz-agent

A small single-agent Python CLI for `dpj135/Orz-agent`.

## Run

Python 3.11+ is required.

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
