"""Application settings. Secrets (API keys) are read from the environment only and never persisted."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"
ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODEMIND_", env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'codemind.db'}"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = ROOT_DIR / "data"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    auth_required: bool = False
    admin_token: str = ""

    llm_provider: str = "none"  # none | openai_compatible | anthropic
    llm_model: str = ""
    llm_api_key: str = Field(default="", repr=False)
    llm_base_url: str = ""
    embedding_provider: str = "hashing"  # hashing (deterministic, offline) | openai_compatible

    sandbox_mode: str = "auto"  # auto | docker | local
    sandbox_timeout_seconds: int = 120
    sandbox_network: bool = False
    # Running repository code directly on the host (restricted subprocess) is OFF unless explicitly enabled.
    allow_local_sandbox: bool = False
    sandbox_cpus: float = 1.0
    sandbox_pids: int = 256
    sandbox_memory_mb: int = 1024
    sandbox_docker_image: str = "python:3.12-slim"

    max_file_bytes: int = 1_000_000
    max_files_per_repo: int = 20_000
    default_context_budget_chars: int = 24_000

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.workspaces_dir.mkdir(parents=True, exist_ok=True)
    s.exports_dir.mkdir(parents=True, exist_ok=True)
    return s
