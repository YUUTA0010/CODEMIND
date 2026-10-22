# CodeMind

AI-assisted software engineering research platform: dependency-aware hybrid code retrieval, bug localization, and **test-verified** automated repair, with a reproducible evaluation framework.

> **Status and honesty note.** This is an undergraduate research project. The one experiment included (`results/synthetic_baseline/`) uses **6 synthetic tasks** and is **inconclusive by design**: in it, the dependency-aware hybrid did *not* beat the conventional hybrid on top-1 or MRR. No claim of improvement is made anywhere in this repository. See [docs/research_methodology.md](docs/research_methodology.md).

## What it does
1. **Index** a local repository (Python via `ast`; JS/TS/TSX via Tree-sitter) into symbols, chunks and a typed dependency graph (IMPORTS, DEFINES, CALLS, INHERITS, REFERENCES, TESTS). Every edge is labeled CONFIRMED / INFERRED / UNRESOLVED; unresolved edges are never traversed.
2. **Retrieve** with lexical (SQLite FTS5 BM25), semantic, symbol, graph, test and stack-trace signals, fused by a transparent weighted sum with an ablation switch per signal.
3. **Localize** a bug from a report, stack trace and optional failing tests: ranked suspects with evidence, uncertainty notes and related tests. Confidence labels are heuristic and uncalibrated.
4. **Analyze and propose** with an optional LLM (OpenAI-compatible or Anthropic). Without a model, a deterministic summary is produced. Citations to files or lines that are not in the index are rejected.
5. **Verify** a patch in a disposable copy: baseline run, patched targeted tests, then the broad suite. A patch is "verified" only if tests ran and passed. Exporting or applying a patch needs explicit approval.
6. **Evaluate** methods on benchmark datasets with seeds, bootstrap confidence intervals, paired sign-flip tests and CSV/JSON/PNG exports.

## Quick start
```bash
python -m venv .venv && . .venv/bin/activate          # Windows: see docs/setup_windows.md
pip install -r requirements.txt
cp .env.example .env                                   # optional
python -m pytest -q                                    # core suite needs no network or paid API
cd frontend && npm install && npm run build && cd ..   # builds frontend/dist
python scripts/run_backend.py                          # http://127.0.0.1:8000 (serves the built UI)
```
Open http://127.0.0.1:8000, then **Repositories → Load synthetic demo** (clearly labeled, isolated) or register your own folder.

Development UI with hot reload: `cd frontend && npm run dev` (http://127.0.0.1:5173, proxies `/api` to the backend).

## Running tests
| Command | What it covers |
|---|---|
| `python -m pytest -q` | 154 backend tests (unit, integration, security, end-to-end HTTP workflow) |
| `cd frontend && npm test` | frontend unit tests |
| `cd frontend && npm run build` | TypeScript check + production build |

## Sandbox (read this before verifying patches)
Patch verification runs repository tests, which is **running untrusted code**.
- **Docker mode** (`--network none`, memory/CPU/pids limits, read-only mounts, dropped capabilities) is the intended safe path. It is implemented but **has not been exercised in the development environment** (no Docker daemon was available).
- **Local mode** (Linux/macOS only) is a restricted subprocess (resource limits, scrubbed environment, process-group kill). It gives **no filesystem or network isolation** and is **off unless** `CODEMIND_ALLOW_LOCAL_SANDBOX=true`.
- With neither, verification returns `VERIFICATION_INCOMPLETE` rather than pretending.

## Reproduce the reference experiment
```bash
CODEMIND_DATA_DIR=./data CODEMIND_ALLOW_LOCAL_SANDBOX=true python scripts/run_backend.py &
curl -X POST localhost:8000/api/v1/experiments -H 'content-type: application/json' \
  -d '{"name":"synthetic baseline","dataset":{"kind":"synthetic"},"ablation_of":"dependency_aware_hybrid","seed":0}'
```
Exports appear in `data/exports/experiment-<id>/`. The committed copy is in `results/synthetic_baseline/`.

## Known limitations
- Default embeddings are a deterministic **hashing** embedder (sub-word overlap), **not neural**; the UI says so.
- The synthetic suite (6 tasks, one small package) is a pipeline test, not evidence about real software.
- The SWE-bench adapter reads a local JSONL and local clones; it was tested only on a synthetic JSONL. `resolved` is an approximation (baseline reproduced AND targeted pass AND broad pass), not the official harness.
- The LLM repair stage is untested against a real model here; it is covered with a scripted test double.
- Call resolution is name-based static analysis: dynamic dispatch, reflection and ambiguous names become UNRESOLVED and are excluded.
- Local file-system access control is by path policy only; deploy behind authentication if not bound to localhost (`scripts/run_backend.py` refuses non-local binds without auth).
- Tests that need file-permission failures are skipped when running as root (1 skipped test).

## Documentation
[architecture](docs/architecture.md) · [research methodology](docs/research_methodology.md) · [evaluation protocol](docs/evaluation_protocol.md) · [benchmark guide](docs/benchmark_guide.md) · [threat model](docs/threat_model.md) · [API reference](docs/api_reference.md) · [Windows / VS Code setup](docs/setup_windows.md)
