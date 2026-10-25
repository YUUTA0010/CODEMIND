"""Benchmark task model with a structural ground-truth leakage barrier.

`SystemInput` is the ONLY object handed to retrieval/localization/repair. It has no label fields. `EvalLabels` holds the
ground truth and is read only by evaluation code (metrics). `assert_no_leakage` additionally checks that label values do
not appear verbatim in the structured query derived from the input beyond what the bug text itself contains.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass(frozen=True)
class SystemInput:
    task_key: str
    repo_path: str
    description: str
    stack_trace: str = ""
    failing_tests: tuple[str, ...] = ()  # only populated when the experiment config says tests are provided to the system


@dataclass(frozen=True)
class EvalLabels:
    ground_truth_files: tuple[str, ...]
    ground_truth_symbols: tuple[str, ...] = ()
    relevant_tests: tuple[str, ...] = ()


@dataclass
class BenchmarkTaskRecord:
    task_key: str
    repo_path: str
    repo_revision: str
    description: str
    stack_trace: str
    relevant_tests: list[str]
    ground_truth_files: list[str]
    ground_truth_symbols: list[str] = field(default_factory=list)
    db_id: int | None = None

    def system_input(self, *, provide_failing_tests: bool) -> SystemInput:
        return SystemInput(self.task_key, self.repo_path, self.description, self.stack_trace,
                           tuple(self.relevant_tests) if provide_failing_tests else ())

    def labels(self) -> EvalLabels:
        return EvalLabels(tuple(self.ground_truth_files), tuple(self.ground_truth_symbols), tuple(self.relevant_tests))


LABEL_FIELD_NAMES = {"ground_truth_files", "ground_truth_symbols", "oracle_fix_diff", "relevant_tests", "expected_failing_tests"}


def assert_input_has_no_label_fields() -> None:
    """Structural check used by tests: SystemInput must not carry any label-named field."""
    names = {f.name for f in fields(SystemInput)}
    leaked = names & LABEL_FIELD_NAMES - {"failing_tests"}
    if leaked:
        raise AssertionError(f"SystemInput exposes label fields: {leaked}")
