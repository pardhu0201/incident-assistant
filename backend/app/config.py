"""Application settings.

Every setting has a default that works with no external services: SQLite for
storage, a dependency-free hashed embedder for retrieval, and in-process span
capture for observability, so the app boots into a free, deterministic "demo"
mode with nothing configured. Supplying a Postgres URL, an Anthropic key, or
an OTLP endpoint upgrades the same code paths in place.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
RUNTIME_DIR = DATA_DIR / "runtime"
SEED_CORPUS_DIR = DATA_DIR / "seed_corpus"
SAMPLE_LOGS_DIR = DATA_DIR / "sample_logs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM -----------------------------------------------------------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    llm_effort: str = "medium"
    llm_max_tokens: int = 4000
    llm_timeout_seconds: float = 120.0

    # --- Database ----------------------------------------------------------
    database_url: str = ""

    # --- Retrieval ---------------------------------------------------------
    embedding_provider: str = "hashing"
    embedding_dim: int = 384
    retrieval_top_k: int = 6
    retrieval_candidates: int = 24
    chunk_size: int = 800
    chunk_overlap: int = 120

    # --- Incident detection --------------------------------------------
    incident_window_seconds: int = 900
    confidence_threshold: float = 0.55

    # --- OpenTelemetry -------------------------------------------------------
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "incident-assistant"

    # --- App -----------------------------------------------------------
    app_env: str = "local"
    seed_on_startup: bool = True
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Optional regex for extra origins, e.g. your own Vercel previews:
    # ^https://incident-assistant(-[a-z0-9-]+)?\.vercel\.app$
    # Empty by default: trusting every *.vercel.app site would let any Vercel
    # project call this API from a visitor's browser.
    cors_origin_regex: str = ""
    serve_frontend: bool = True
    # When set, approval decisions (which execute fixes) need an X-Admin-Token header.
    admin_token: str = ""

    @field_validator("llm_effort")
    @classmethod
    def _valid_effort(cls, v: str) -> str:
        allowed = {"low", "medium", "high", "xhigh", "max"}
        v = v.strip().lower()
        if v not in allowed:
            raise ValueError(f"llm_effort must be one of {sorted(allowed)}")
        return v

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    @property
    def llm_mode(self) -> str:
        return "claude" if self.llm_enabled else "demo"

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url.strip():
            return _normalise_pg_url(self.database_url.strip())
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(RUNTIME_DIR / 'app.db').as_posix()}"

    @property
    def is_postgres(self) -> bool:
        return self.sqlalchemy_url.startswith("postgresql")

    @property
    def cors_origin_list(self) -> list[str]:
        raw = [o.strip() for o in self.cors_origins.split(",")]
        return [o for o in raw if o] or ["*"]


def _normalise_pg_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
