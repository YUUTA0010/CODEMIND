"""Embedding providers.

* HashingEmbedder - deterministic, offline, no model download. It hashes identifier sub-tokens and bigrams into a
  fixed-size vector (the "hashing trick"). It captures *sub-word vocabulary overlap*, NOT neural semantics, so it must
  not be presented as equivalent to a trained embedding model. It exists so the whole pipeline (and the automated
  tests) work without network access or paid APIs.
* OpenAICompatibleEmbedder - calls any `/embeddings` endpoint compatible with the OpenAI API. The API key is taken from
  the process environment (CODEMIND_LLM_API_KEY) and is never stored in the database.
"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol

import numpy as np

from app.core.config import Settings, get_settings
from app.retrieval.text import tokenize


class Embedder(Protocol):
    name: str
    dim: int
    is_neural: bool

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class HashingEmbedder:
    name = "hashing-v1"
    is_neural = False

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _slot(self, token: str) -> tuple[int, float]:
        h = hashlib.blake2b(token.encode(), digest_size=8).digest()
        v = int.from_bytes(h, "little")
        return v % self.dim, 1.0 if (v >> 40) & 1 else -1.0

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            toks = tokenize(text)
            counts: dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            for a, b in zip(toks, toks[1:]):
                bg = f"{a}\x1f{b}"
                counts[bg] = counts.get(bg, 0) + 1
            for tok, c in counts.items():
                slot, sign = self._slot(tok)
                weight = (1.0 + math.log(c)) * (0.5 if "\x1f" in tok else 1.0)
                out[i, slot] += sign * weight
        return _normalize(out)


class OpenAICompatibleEmbedder:
    is_neural = True

    def __init__(self, base_url: str, model: str, api_key: str, dim: int = 0, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dim = dim
        self.timeout = timeout
        self.name = f"openai-compatible:{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        import httpx

        vectors: list[list[float]] = []
        with httpx.Client(timeout=self.timeout) as client:
            for start in range(0, len(texts), 64):
                batch = [t[:8000] for t in texts[start : start + 64]]
                r = client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "input": batch},
                )
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                vectors.extend(d["embedding"] for d in data)
        arr = np.array(vectors, dtype=np.float32)
        self.dim = arr.shape[1]
        return _normalize(arr)


def get_embedder(settings: Settings | None = None) -> Embedder:
    s = settings or get_settings()
    if s.embedding_provider == "openai_compatible" and s.llm_api_key and s.llm_base_url:
        return OpenAICompatibleEmbedder(s.llm_base_url, s.llm_model or "text-embedding-3-small", s.llm_api_key)
    return HashingEmbedder()


def to_bytes(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_bytes(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32)
