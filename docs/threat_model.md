# Threat model

CodeMind indexes and runs code it did not write. Assume every repository is hostile unless you know otherwise.

| Asset | Threat | Mitigation | Residual risk |
|---|---|---|---|
| Host machine | Repository tests/code execute during verification | Docker sandbox (no network, memory/CPU/pids limits, read-only, cap-drop). Local subprocess mode is **off by default** (`CODEMIND_ALLOW_LOCAL_SANDBOX`), uses rlimits, scrubbed env, process-group kill; no safe sandbox → `VERIFICATION_INCOMPLETE` | Local mode has **no filesystem/network isolation**. Docker mode is implemented but was not exercised in development |
| Original repository | Overwrite/deletion by ingestion or patching | Ingestion is read-only; patches apply in a disposable copy; applying to the working tree needs explicit approval and path policy | A user who approves applying a bad patch changes their tree |
| Model prompts | Prompt injection via code, comments, README, issues, config | All repo text is fenced as untrusted data; output must validate against a schema; citations must be real indexed files and valid line ranges (others go to `rejected_citations`); a patch still must pass tests | Injection can still bias model text; treat all model output as unverified |
| Patches | Path traversal, absolute/protected paths, symlinks, renames, binary diffs | Strict own parser: no fuzz, two-phase apply, extension allow-list, symlink check | Allowed edits can still be harmful code |
| Secrets | API key leak | Keys only from environment; never in DB, API responses, logs or runtime settings file (tested); `PUT /settings` rejects key fields | Process environment is visible to the operator |
| API | Unauthenticated access, privilege escalation | Optional bearer tokens (SHA-256 hashed), roles viewer < developer < admin, admin token from env, audit log, WebSocket auth via `?token=`, launcher refuses non-local bind without auth | No rate limiting, TLS, or token expiry; put behind a reverse proxy if exposed |
| File endpoint | Reading files outside the index | `safe_join`, indexed-path check, traversal tests | — |
| Evaluation | Label leakage inflating results | `SystemInput` has no label fields; tests prove labels/oracle fix are absent from retrieval and prompts | Authors of a benchmark may still bias it |
| Claims | False "fixed" claims | Status only becomes `TESTS_PASSED` after real runs; reports always list verification limits | Passing tests do not prove correctness |

Not in scope: multi-tenant isolation, supply-chain attacks on dependencies, side channels.
