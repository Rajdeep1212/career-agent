from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "Career Agent"

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

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
