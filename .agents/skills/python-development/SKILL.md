---
name: python-development
description: Use when changing Python code, tests, or package dependencies in Orz-agent.
---
# Python development workflow

1. Read applicable workspace guidance and inspect existing callers before editing.
2. Keep CLI, model requests, work loop, and permission enforcement separate. Keep the product single-agent.
3. Use explicit types at public boundaries and validate untrusted inputs at runtime. Never use eval or shell=True.
4. Add deterministic regression tests for changed behavior. Use fake models and temporary workspaces; ordinary tests must not contact a model API.
5. Run python -m pytest and report actual outcomes, including skipped platform cases. Do not claim untested support.
6. Update documentation when observable behavior or dependencies change. Before a requested commit, inspect the diff for credentials and unrelated changes; use git commit -s. Do not commit or push without authorization.
