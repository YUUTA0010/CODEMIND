"""Request/response schemas for the v1 API. All inputs are validated and size-bounded."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.retrieval.types import METHODS, SIGNALS


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


class RepositoryCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    name: str | None = Field(default=None, max_length=200)
    auto_index: bool = False
    ignore_patterns: list[str] = Field(default_factory=list, max_length=100)


class IndexRequest(BaseModel):
    mode: Literal["incremental", "full"] = "incremental"


class RetrievalOptions(BaseModel):
    """Per-request retrieval overrides. Unknown weight/signal names are rejected."""

    model_config = ConfigDict(extra="forbid")
    method: Literal["lexical", "semantic", "hybrid_conventional", "dependency_aware", "hybrid_dependency", "adaptive"] = "hybrid_dependency"
    weights: dict[str, float] = Field(default_factory=dict)
    disabled_signals: list[str] = Field(default_factory=list)
    top_k: int = Field(default=20, ge=1, le=100)
    budget_chars: int | None = Field(default=None, ge=500, le=500_000)
    graph_depth: int | None = Field(default=None, ge=0, le=4)
    include_tests_in_context: bool = True

    @field_validator("weights")
    @classmethod
    def _weights(cls, v):
        for k, x in v.items():
            if k not in SIGNALS:
                raise ValueError(f"unknown signal '{k}'; valid: {SIGNALS}")
            if not (0.0 <= x <= 10.0):
                raise ValueError("weights must be between 0 and 10")
        return v

    @field_validator("disabled_signals")
    @classmethod
    def _disabled(cls, v):
        bad = [x for x in v if x not in SIGNALS]
        if bad:
            raise ValueError(f"unknown signals: {bad}")
        return v


class SearchRequest(BaseModel):
    repository_id: int
    query: str = Field(min_length=1, max_length=5000)
    mode: Literal["symbol", "lexical", "semantic", "hybrid", "dependency"] = "hybrid"
    limit: int = Field(default=20, ge=1, le=100)
    disabled_signals: list[str] = Field(default_factory=list)

    @field_validator("disabled_signals")
    @classmethod
    def _disabled(cls, v):
        bad = [x for x in v if x not in SIGNALS]
        if bad:
            raise ValueError(f"unknown signals: {bad}")
        return v


class InvestigationCreate(BaseModel):
    repository_id: int
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    stack_trace: str = Field(default="", max_length=50_000)
    error_message: str = Field(default="", max_length=5_000)
    failing_tests: list[str] = Field(default_factory=list, max_length=50)
    reproduction_steps: str = Field(default="", max_length=10_000)
    expected_behavior: str = Field(default="", max_length=5_000)
    observed_behavior: str = Field(default="", max_length=5_000)

    @field_validator("failing_tests")
    @classmethod
    def _tests(cls, v):
        for t in v:
            if len(t) > 300 or t.startswith("-") or "\n" in t:
                raise ValueError(f"invalid test identifier: {t[:60]!r}")
        return v

    def has_content(self) -> bool:
        return any([self.description.strip(), self.stack_trace.strip(), self.error_message.strip(), self.failing_tests])


class RetrieveRequest(BaseModel):
    options: RetrievalOptions = Field(default_factory=RetrievalOptions)


class AnalyzeRequest(BaseModel):
    use_model: bool = True
    max_iterations: int = Field(default=3, ge=1, le=6)


class PatchCreate(BaseModel):
    diff: str | None = Field(default=None, max_length=200_000)
    rationale: str = Field(default="", max_length=4000)
    from_analysis: bool = False

    def validate_source(self) -> None:
        if bool(self.diff) == bool(self.from_analysis):
            raise ValueError("provide exactly one of: diff, from_analysis=true")


class VerifyRequest(BaseModel):
    targeted_tests: list[str] | None = Field(default=None, max_length=50)
    run_broad: bool = True


class ExportRequest(BaseModel):
    approve: bool = False
    apply_to_working_tree: bool = False


class DatasetSpec(BaseModel):
    kind: Literal["synthetic", "directory", "swebench"] = "synthetic"
    path: str | None = Field(default=None, max_length=1000)  # directory root, or SWE-bench JSONL
    repos_dir: str | None = Field(default=None, max_length=1000)  # SWE-bench: local clones
    limit: int | None = Field(default=None, ge=1, le=5000)


class MethodSpecIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=80)
    method: Literal["lexical", "semantic", "hybrid_conventional", "dependency_aware", "hybrid_dependency", "adaptive"]
    weights: dict[str, float] = Field(default_factory=dict)
    disabled_signals: list[str] = Field(default_factory=list)


class SplitSpec(BaseModel):
    test_fraction: float = Field(default=0.5, gt=0.0, lt=1.0)
    tune_weights: bool = False
    tune_method: str = "dependency_aware_hybrid"
    trials: int = Field(default=25, ge=1, le=500)


class RepairSpec(BaseModel):
    enabled: bool = False
    method_label: str | None = None
    max_iterations: int = Field(default=3, ge=1, le=6)


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dataset: DatasetSpec = Field(default_factory=DatasetSpec)
    methods: list[MethodSpecIn] | None = None
    ablation_of: str | None = None
    reference_method: str | None = None
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    repeats: int = Field(default=1, ge=1, le=20)
    provide_failing_tests: bool = False
    retrieval: dict[str, Any] = Field(default_factory=dict)
    split: SplitSpec | None = None
    repair: RepairSpec = Field(default_factory=RepairSpec)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    llm_provider: Literal["none", "openai_compatible", "anthropic"] | None = None
    llm_model: str | None = Field(default=None, max_length=200)
    llm_base_url: str | None = Field(default=None, max_length=500)
    embedding_provider: Literal["hashing", "openai_compatible"] | None = None
    retrieval_weights: dict[str, float] | None = None
    context_budget_chars: int | None = Field(default=None, ge=1000, le=500_000)
    graph_depth: int | None = Field(default=None, ge=0, le=4)
    sandbox_timeout_seconds: int | None = Field(default=None, ge=5, le=3600)
    sandbox_memory_mb: int | None = Field(default=None, ge=128, le=16384)
    sandbox_network: bool | None = None
    sandbox_mode: Literal["auto", "docker", "local"] | None = None

    @field_validator("retrieval_weights")
    @classmethod
    def _w(cls, v):
        if v is None:
            return v
        for k, x in v.items():
            if k not in SIGNALS or not (0.0 <= x <= 10.0):
                raise ValueError(f"invalid retrieval weight: {k}={x}")
        return v


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    role: Literal["admin", "developer", "viewer"] = "viewer"


__all__ = [n for n in dir() if not n.startswith("_")] + ["METHODS"]
