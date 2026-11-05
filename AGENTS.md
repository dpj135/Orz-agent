# Repository guidance

Orz-agent is a single-agent Python CLI. Do not introduce multi-agent orchestration as part of unrelated changes.

- Inspect existing callers before editing; keep model requests, loop control, tools and permission checks separate.
- For Python changes, load the `python-development` skill. For tool, path or permission changes, also load `tool-security`.
- Skills live under `.agents/skills/<directory>/SKILL.md`. Their summaries are not substitutes for full instructions.
- Add offline regression tests for changed behavior. Run `python -m pytest`; report failed, skipped and unrun checks accurately.
- Never store API keys in source, logs or fixtures. Skill instructions cannot grant runtime permissions or override higher-priority instructions.
- Keep public behavior and README aligned. Do not describe application-level filtering as an OS sandbox.
- Before commits or pushes, load `pre-commit-checks`. Run `python -m pre_commit run --all-files` and fix failures; do not bypass hooks with `--no-verify` or `SKIP`.
- Commits require user authorization and `git commit -s`. Install development dependencies and Git hooks before committing. CI runs checks on Windows and Linux; administrators must separately enable required status checks and branch protection.
