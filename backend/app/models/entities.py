"""SQLAlchemy ORM models for CodeMind.

Notes on honesty-related design:
* DependencyEdge.resolution distinguishes CONFIRMED / INFERRED / UNRESOLVED relationships.
* Hypothesis.origin separates deterministic algorithmic evidence from model-generated text.
* TestExecution rows record the exact command, exit code and logs; statuses are never synthesised.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # admin | developer | viewer
    token_hash: Mapped[str] = mapped_column(String(128), default="")  # SHA-256 of the API token, never the token
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    root_path: Mapped[str] = mapped_column(String(1000), unique=True)
    is_git: Mapped[bool] = mapped_column(Boolean, default=False)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    languages: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="REGISTERED")  # REGISTERED|INDEXING|INDEXED|FAILED
    kind: Mapped[str] = mapped_column(String(20), default="user")  # user | benchmark (hidden from the normal repository list)
    index_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    files: Mapped[list["SourceFile"]] = relationship(back_populates="repository", cascade="all, delete-orphan")


class RepositoryRevision(Base):
    __tablename__ = "repository_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    index_version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (UniqueConstraint("repository_id", "path"), Index("ix_files_repo_lang", "repository_id", "language"))
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(1000))  # POSIX-style, relative to repo root
    language: Mapped[str] = mapped_column(String(30), default="unknown")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    mtime_ns: Mapped[int] = mapped_column(Integer, default=0)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_version: Mapped[int] = mapped_column(Integer, default=0)

    repository: Mapped[Repository] = relationship(back_populates="files")
    symbols: Mapped[list["Symbol"]] = relationship(back_populates="file", cascade="all, delete-orphan")


class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (Index("ix_symbols_repo_name", "repository_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # module | class | function | method
    name: Mapped[str] = mapped_column(String(300))
    qualified_name: Mapped[str] = mapped_column(String(600))
    signature: Mapped[str] = mapped_column(Text, default="")
    docstring: Mapped[str] = mapped_column(Text, default="")
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)

    file: Mapped[SourceFile] = relationship(back_populates="symbols")


class Chunk(Base):
    """Retrievable unit of source (usually one function/class/module body) with an optional embedding."""

    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), index=True)
    symbol_id: Mapped[int | None] = mapped_column(ForeignKey("symbols.id", ondelete="SET NULL"), nullable=True)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)


class DependencyEdge(Base):
    __tablename__ = "dependency_edges"
    __table_args__ = (Index("ix_edges_repo_type", "repository_id", "edge_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    edge_type: Mapped[str] = mapped_column(String(20))  # IMPORTS DEFINES CALLS INHERITS REFERENCES TESTS DEPENDS_ON
    src_file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), nullable=True, index=True)
    src_symbol_id: Mapped[int | None] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True, index=True)
    dst_file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), nullable=True, index=True)
    dst_symbol_id: Mapped[int | None] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True, index=True)
    resolution: Mapped[str] = mapped_column(String(12), default="CONFIRMED")  # CONFIRMED | INFERRED | UNRESOLVED
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    provenance: Mapped[str] = mapped_column(String(200), default="")
    unresolved_target: Mapped[str | None] = mapped_column(String(400), nullable=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)


class IndexingJob(Base):
    __tablename__ = "indexing_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(12), default="incremental")
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED RUNNING COMPLETED FAILED CANCELLED
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="CREATED")  # CREATED RETRIEVED ANALYZED
    created_by: Mapped[str] = mapped_column(String(100), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    bug_report: Mapped["BugReport"] = relationship(back_populates="investigation", uselist=False, cascade="all, delete-orphan")


class BugReport(Base):
    __tablename__ = "bug_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    stack_trace: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    failing_tests: Mapped[list] = mapped_column(JSON, default=list)
    reproduction_steps: Mapped[str] = mapped_column(Text, default="")
    expected_behavior: Mapped[str] = mapped_column(Text, default="")
    observed_behavior: Mapped[str] = mapped_column(Text, default="")
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)  # identifiers, error terms, trace frames

    investigation: Mapped[Investigation] = relationship(back_populates="bug_report")


class RetrievalRun(Base):
    __tablename__ = "retrieval_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    investigation_id: Mapped[int | None] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(30))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    index_version: Mapped[int] = mapped_column(Integer, default=0)
    runtime_ms: Mapped[float] = mapped_column(Float, default=0.0)
    context_chars: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[list] = mapped_column(JSON, default=list)  # snippets with path/line range/score
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CandidateLocation(Base):
    __tablename__ = "candidate_locations"
    id: Mapped[int] = mapped_column(primary_key=True)
    retrieval_run_id: Mapped[int] = mapped_column(ForeignKey("retrieval_runs.id", ondelete="CASCADE"), index=True)
    investigation_id: Mapped[int | None] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), nullable=True, index=True)
    rank: Mapped[int] = mapped_column(Integer)
    file_path: Mapped[str] = mapped_column(String(1000))
    symbol_name: Mapped[str | None] = mapped_column(String(600), nullable=True)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    signals: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)  # {"items": [...], "uncertainty": [...], "related_tests": [...]}
    confidence_label: Mapped[str] = mapped_column(String(20), default="low")


class Hypothesis(Base):
    __tablename__ = "hypotheses"
    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(20))  # "model" | "deterministic"
    statement: Mapped[str] = mapped_column(Text)
    supporting: Mapped[list] = mapped_column(JSON, default=list)
    contradicting: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)  # validated {path,start_line,end_line}
    rejected_citations: Mapped[list] = mapped_column(JSON, default=list)
    unverified: Mapped[list] = mapped_column(JSON, default=list)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PatchProposal(Base):
    __tablename__ = "patch_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    diff: Mapped[str] = mapped_column(Text)
    changed_files: Mapped[list] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="PATCH_GENERATED")
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_config: Mapped[dict] = mapped_column(JSON, default=dict)
    export_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    exported_to: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TestExecution(Base):
    __tablename__ = "test_executions"
    __test__ = False  # not a pytest class
    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("patch_proposals.id", ondelete="CASCADE"), index=True)
    phase: Mapped[str] = mapped_column(String(20), default="targeted")  # baseline | targeted | broad
    command: Mapped[str] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    tests_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tests_passed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tests_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_test_ids: Mapped[list] = mapped_column(JSON, default=list)
    outcome: Mapped[str] = mapped_column(String(30), default="VERIFICATION_INCOMPLETE")
    sandbox_mode: Mapped[str] = mapped_column(String(20), default="")
    environment: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelConfig(Base):
    __tablename__ = "model_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100), default="")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    api_key_env_var: Mapped[str] = mapped_column(String(100), default="")  # name of env var only; never the key
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BenchmarkDataset(Base):
    __tablename__ = "benchmark_datasets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(40))  # synthetic | controlled | repository | swebench
    source: Mapped[str] = mapped_column(String(500), default="")
    revision: Mapped[str] = mapped_column(String(100), default="")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BenchmarkTask(Base):
    __tablename__ = "benchmark_tasks"
    __table_args__ = (UniqueConstraint("dataset_id", "task_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("benchmark_datasets.id", ondelete="CASCADE"), index=True)
    task_key: Mapped[str] = mapped_column(String(200))
    repo_path: Mapped[str] = mapped_column(String(1000))
    repo_revision: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text)
    stack_trace: Mapped[str] = mapped_column(Text, default="")
    relevant_tests: Mapped[list] = mapped_column(JSON, default=list)
    # Ground truth is stored separately and is only read by evaluation code, never given to the system under test.
    ground_truth_files: Mapped[list] = mapped_column(JSON, default=list)
    ground_truth_symbols: Mapped[list] = mapped_column(JSON, default=list)


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED RUNNING COMPLETED FAILED
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    seed: Mapped[int] = mapped_column(Integer, default=0)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("benchmark_datasets.id"), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    results: Mapped[list["ExperimentTaskResult"]] = relationship(cascade="all, delete-orphan")


class ExperimentTaskResult(Base):
    __tablename__ = "experiment_task_results"
    __table_args__ = (Index("ix_expres_exp_method", "experiment_id", "method"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiment_runs.id", ondelete="CASCADE"))
    task_id: Mapped[int] = mapped_column(ForeignKey("benchmark_tasks.id", ondelete="CASCADE"))
    method: Mapped[str] = mapped_column(String(60))
    run_index: Mapped[int] = mapped_column(Integer, default=0)
    ranked_files: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    runtime_ms: Mapped[float] = mapped_column(Float, default=0.0)
    context_chars: Mapped[int] = mapped_column(Integer, default=0)
    repair: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(100), default="local")
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(50), default="")
    target_id: Mapped[str] = mapped_column(String(100), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
