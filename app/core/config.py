from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "Career Agent"
    # The one browser origin trusted for mutations (scheme://host:port, no path).
    app_origin: str = "http://localhost:8010"

    # Job search provider
    rapidapi_key: str | None = None
    rapidapi_host: str = "jsearch.p.rapidapi.com"
    job_providers: str = "jsearch"
    search_country: str = "in"
    verification_concurrency: int = 4

    # HTTP safety / verification
    request_timeout_seconds: float = 20.0
    verify_ssl: bool = True

    # Google OAuth / Gmail
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://127.0.0.1:8010/auth/google/callback"

    # LinkedIn OIDC (local, single-user connection).
    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    linkedin_redirect_uri: str = "http://localhost:8010/auth/linkedin/callback"

    # Local encryption key used to encrypt OAuth tokens at rest.
    # Generate with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str | None = None

    # Optional chat-model assistance. Deterministic routing remains authoritative.
    chat_model_provider: str = "none"
    chat_model_name: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"
    gemini_api_key: str | None = None
    gemini_model_name: str = "gemini-3.8-flash"
    chat_model_structured_output: bool = False
    chat_model_tool_calling: bool = False
    chat_model_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    langgraph_checkpoint_path: str = str(BASE_DIR / "data" / "langgraph.sqlite3")

    data_dir: str = str(BASE_DIR / "data")
    upload_dir: str = str(BASE_DIR / "data" / "uploads")

    @field_validator("app_origin")
    @classmethod
    def _plain_origin(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        try:
            parts = urlsplit(value)
            parts.port
        except ValueError as exc:
            raise ValueError("APP_ORIGIN must look like http://localhost:8010") from exc
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.path
                or parts.query or parts.fragment or parts.username or parts.password):
            raise ValueError("APP_ORIGIN must look like http://localhost:8010")
        return value

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
