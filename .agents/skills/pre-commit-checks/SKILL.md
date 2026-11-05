---
name: pre-commit-checks
description: Use before committing or pushing changes, or when modifying development checks and CI in Orz-agent.
---
# Commit checks workflow

1. Confirm user authorization to commit or push. Inspect branch, status and diff; exclude credentials, generated artifacts and unrelated changes.
2. Install the repository development dependencies in an active virtual environment with `python -m pip install -e ".[dev]"`. Install the Git hook with `python -m pre_commit install`.
3. Add regression tests for changed behavior. Ordinary tests must use fake models, temporary workspaces and no real API credentials.
4. Run `python -m pre_commit run --all-files`. All hooks must pass. If a hook modifies files, inspect those changes, re-stage the intended files and rerun checks until they pass.
5. Do not use `--no-verify`, `SKIP` or weakened checks to bypass failures. Report environmental blockers rather than claiming success.
6. Stage only intended changes and commit with `git commit -s`. Confirm the installed commit hook succeeds and inspect the resulting commit.
7. Push only when requested; create a PR reporting changes, actual checks, skipped platform coverage and limitations. Windows/Linux CI must pass before merge.
8. Local hooks can be bypassed. Required CI status checks and main-branch protection must be configured by repository administrators; do not claim they are enabled without verifying.
