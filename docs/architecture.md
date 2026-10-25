# Architecture

```
frontend (React/Vite/TS)  --HTTP /api/v1 + WebSocket-->  FastAPI app (backend/app)
                                                         |
   api/  ->  indexing/ parsing/ graph/ retrieval/ localization/ agents/ patches/ sandbox/ experiments/
                                                         |
                                       SQLAlchemy -> SQLite (WAL, FK on, FTS5 chunk_fts)
```

## Modules (`backend/app/`)
| Module | Responsibility |
|---|---|
| `core/` | settings (`CODEMIND_*` env), structured errors, in-process event bus, job pool, bearer auth with roles, runtime (non-secret) settings |
| `db/`, `models/` | engine/session, FTS5 creation, entities (Repository, SourceFile, Symbol, Chunk, DependencyEdge, IndexingJob, Investigation, BugReport, RetrievalRun, CandidateLocation, Hypothesis, PatchProposal, TestExecution, Benchmark*, ExperimentRun, AuditEvent, ...) |
| `parsing/` | normalized IR (`ParsedFile/Symbol/Import/Reference`); Python `ast`; Tree-sitter JS/TS/TSX; language and test-file detection |
| `indexing/` | repository root detection, ignore rules, walking, fingerprinting, indexer. Phase A (read, parse, embed in memory) then Phase B (a single DB transaction), so cancellation never leaves a half-written index. Incremental mode uses stat + sha256 and re-resolves all edges globally |
| `graph/` | `Resolver` (imports/aliases/calls to labeled edges) and `RepoGraph` (callers, callees, neighborhoods, paths, components, centrality; cached by index version + fingerprint) |
| `retrieval/` | chunking, FTS5 BM25 (with pure-Python fallback), vector index (numpy brute-force cosine), signals, fusion, adaptive expansion, budgeted context selection |
| `localization/` | bug-report parsing (stack traces), candidate ranking with evidence/uncertainty/related tests |
| `agents/` | provider-independent LLM interface (Null, OpenAI-compatible, Anthropic, scripted test double), validated JSON turns, bounded loop, citation validation, untrusted-content fencing |
| `patches/` | strict unified-diff parser/applier, path policy, lifecycle statuses, verification flow, reports, export |
| `sandbox/` | docker or restricted-local execution, pytest parsing, outcome classification |
| `experiments/` | metrics, statistics, task adapters, runner, reporting/exports |

## Key design decisions
- **Epistemic separation.** Facts (index contents, test results), heuristics (scores, confidence labels) and model text are stored and displayed separately; the UI marks each.
- **Edge honesty.** Ambiguous name-only calls become UNRESOLVED with no target and are excluded from graph traversal and scoring.
- **Fusion.** Per-signal max normalization, then a weight-normalized linear sum. Defaults: lexical .30, symbol_exact .15, semantic .20, graph .15, test .10, trace .10, history 0. `disabled_signals` is the ablation switch. The graph signal uses bounded propagation with hub damping and excludes a seed's own credit.
- **Context selection** is budgeted and deduplicated; every omitted candidate is reported with a reason and truncation is flagged.
- **Leakage barrier.** Systems under test receive a frozen `SystemInput` with no label fields; labels live in `EvalLabels` and are read only by scoring code. Tests assert that labels and the oracle fix never reach retrieval or model prompts.
- **Never trust a patch because a model wrote it.** Statuses: PATCH_GENERATED, PATCH_APPLIED_IN_SANDBOX, TESTS_PASSED, TESTS_FAILED, TESTS_TIMED_OUT, VERIFICATION_INCOMPLETE, PATCH_REJECTED.
- **Original repositories are read-only** during ingestion and verification; verification runs in a disposable copy. Applying to the working tree is a separate, explicitly approved step.

## Events
`index.*`, `verify.*`, `experiment.*` events flow through the bus to `WebSocket /api/v1/ws/events` and drive live UI refresh.

## Database
SQLite by default. `CODEMIND_DATABASE_URL` accepts other SQLAlchemy URLs (e.g. `postgresql+psycopg://...`), but PostgreSQL has **not been tested**: the FTS5 virtual table is SQLite-specific, and lexical search falls back to an in-process BM25 implementation when FTS5 is unavailable. Schema is created with `create_all` at startup; there are no migrations yet, so a schema change requires re-creating `data/codemind.db`.
