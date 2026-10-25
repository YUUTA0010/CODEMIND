"""Runtime-adjustable, NON-SECRET settings persisted to data/runtime_settings.json. API keys are never written here."""
from __future__ import annotations

import json
import threading

from app.core.config import get_settings
from app.retrieval.types import DEFAULT_WEIGHTS, RetrievalConfig

_lock = threading.Lock()
_FIELDS_ON_SETTINGS = {"llm_provider", "llm_model", "llm_base_url", "embedding_provider", "sandbox_timeout_seconds", "sandbox_memory_mb",
                       "sandbox_network", "sandbox_mode"}
_state: dict = {}


def _path():
    return get_settings().data_dir / "runtime_settings.json"


def load() -> dict:
    global _state
    with _lock:
        try:
            _state = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _state = {}
        s = get_settings()
        for k in _FIELDS_ON_SETTINGS & set(_state):
            setattr(s, k, _state[k])
        return dict(_state)


def update(patch: dict) -> dict:
    s = get_settings()
    with _lock:
        for k, v in patch.items():
            if v is None:
                continue
            _state[k] = v
            if k in _FIELDS_ON_SETTINGS:
                setattr(s, k, v)
        try:
            _path().write_text(json.dumps(_state, indent=2), encoding="utf-8")
        except OSError:
            pass
        return dict(_state)


def effective() -> dict:
    s = get_settings()
    w = dict(DEFAULT_WEIGHTS)
    w.update(_state.get("retrieval_weights") or {})
    return {
        "llm_provider": s.llm_provider, "llm_model": s.llm_model, "llm_base_url": s.llm_base_url, "llm_api_key_configured": bool(s.llm_api_key),
        "embedding_provider": s.embedding_provider,
        "retrieval_weights": w, "context_budget_chars": _state.get("context_budget_chars", s.default_context_budget_chars),
        "graph_depth": _state.get("graph_depth", RetrievalConfig().graph_depth),
        "sandbox_mode": s.sandbox_mode, "sandbox_timeout_seconds": s.sandbox_timeout_seconds, "sandbox_memory_mb": s.sandbox_memory_mb,
        "sandbox_network": s.sandbox_network, "allow_local_sandbox": s.allow_local_sandbox,
        "max_file_bytes": s.max_file_bytes, "auth_required": s.auth_required,
    }


def default_retrieval_config(**overrides) -> RetrievalConfig:
    eff = effective()
    cfg = RetrievalConfig(weights=dict(eff["retrieval_weights"]), budget_chars=eff["context_budget_chars"], graph_depth=eff["graph_depth"])
    d = cfg.to_dict()
    for k, v in overrides.items():
        if v is None:
            continue
        if k == "weights":
            d["weights"] = {**d["weights"], **v}
        else:
            d[k] = v
    return RetrievalConfig.from_dict(d)
