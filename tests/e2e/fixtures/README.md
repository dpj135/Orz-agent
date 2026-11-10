# Process-level E2E fixtures

All JSON fixtures here are **synthetic**, manually constructed from the Chat
Completions protocol shape. They are not provider recordings and do not establish
compatibility with a particular provider. No API credentials or real user data
are present. `completion.json` is a final answer; `tool_call.json` is a tool-call
response. Tests clone these fixed samples to exercise error and policy scenarios.

Future recorded fixtures depend on authorized trajectory capture, redaction and
review, tracked in [issue #15](https://github.com/dpj135/Orz-agent/issues/15).
Synthetic edge-case samples should remain even after real samples are added.

Run explicitly from the repository root:

```console
python -m pytest tests/e2e -q
```

The suite only contacts a standard-library HTTP server bound to a random port on
`127.0.0.1`. The CLI itself runs as a real `python -m mini_agent` subprocess;
no application functions are monkeypatched or imported into the harness. Every
case has a private workspace, closes its server, and bounds CLI runtime. CLI
stdout/stderr/status and raw JSON HTTP request bodies (never authorization headers)
are saved under pytest's per-test temporary directory. CI uses
`--basetemp=e2e-artifacts` and `--junitxml=e2e-artifacts/results.xml` and attempts
to upload the diagnostic directory after every run, whether tests pass or fail.
Artifacts are retained for 7 days. The `e2e-artifacts/` directory is ignored by
Git so the same command can be used locally without staging diagnostic files.

The Unicode regression feeds surrogatepass-encoded stdin to the actual REPL,
with an explicit child stdin decoding policy so it exercises the same invalid
Python input string on Windows and Linux. Removing the user-input sanitizer
must make that test fail; merely checking that `json.loads` succeeds would not
catch escaped unpaired surrogates.

These tests verify application-level policy, not an OS security boundary.
