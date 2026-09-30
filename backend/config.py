"""
Application configuration loaded from environment variables.

All secrets and tunable values live here, sourced from .env.
No hardcoded credentials anywhere else.
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


from pathlib import Path

_DATA_DIR = (Path(__file__).parent.parent / "data").resolve()
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DEFAULT_DB_URL = f"sqlite+aiosqlite:///{(_DATA_DIR / 'notetaker.db').as_posix()}"


_ROOT_DIR = Path(__file__).parent.parent.resolve()
_BACKEND_DIR = Path(__file__).parent.resolve()

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_ROOT_DIR / ".env", _BACKEND_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Google Meet OAuth ─────────────────────────────────────────────────
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_refresh_token: Optional[str] = None

    # ── LLM ───────────────────────────────────────────────────────────────
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None  # override for OpenRouter etc.
    llm_model: str = "gpt-4o-mini"

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = _DEFAULT_DB_URL

    # ── Application ───────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
