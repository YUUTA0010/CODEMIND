"""Pluggable vector index. `BruteForceIndex` (exact cosine via numpy) is the default; swap in FAISS/HNSW by implementing
the same two methods and registering it in `make_index`."""
from __future__ import annotations

from typing import Protocol

import numpy as np


class VectorIndex(Protocol):
    def build(self, ids: list[int], matrix: np.ndarray) -> None: ...
    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]: ...


class BruteForceIndex:
    name = "bruteforce-cosine"

    def __init__(self) -> None:
        self.ids: list[int] = []
        self.matrix = np.zeros((0, 0), dtype=np.float32)

    def build(self, ids: list[int], matrix: np.ndarray) -> None:
        self.ids = list(ids)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True) if len(matrix) else matrix
        if len(matrix):
            norms = np.where(norms == 0, 1.0, norms)
            matrix = matrix / norms
        self.matrix = matrix.astype(np.float32)

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if not self.ids:
            return []
        q = np.asarray(query, dtype=np.float32)
        n = np.linalg.norm(q)
        if n == 0:
            return []
        sims = self.matrix @ (q / n)
        k = min(k, len(self.ids))
        # stable ordering: sort by (-score, id) so ties are deterministic across runs
        idx = np.argpartition(-sims, k - 1)[:k] if k < len(sims) else np.arange(len(sims))
        ranked = sorted(((self.ids[i], float(sims[i])) for i in idx), key=lambda t: (-t[1], t[0]))
        return ranked


def make_index(kind: str = "bruteforce") -> VectorIndex:
    if kind == "bruteforce":
        return BruteForceIndex()
    raise ValueError(f"unknown vector index: {kind}")
