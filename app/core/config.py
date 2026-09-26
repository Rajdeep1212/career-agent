import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]

# Credentials and integrations that DEMO_MODE always ignores.
DEMO_BLANKED = (
    "rapidapi_key", "adzuna_app_id", "adzuna_app_key", "jooble_api_key",
    "google_client_id", "google_client_secret", "linkedin_client_id", "linkedin_client_secret",
    "token_encryption_key", "gemini_api_key",
    "alerts_imap_host", "alerts_imap_user", "alerts_imap_app_password",
)


class Settings(BaseSettings):
    app_name: str = "Career Agent"
    # The one browser origin trusted for mutations (scheme://host:port, no path).
    app_origin: str = "http://localhost:8010"

    # Job search provider
    rapidapi_key: str | None = None
    rapidapi_host: str = "jsearch.p.rapidapi.com"
    # Adzuna (https://developer.adzuna.com/) and Jooble (https://jooble.org/api/about).
    # Each provider is optional: without its key it is skipped.
    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None
    jooble_api_key: str | None = None
    # A Jooble key works only on the country site it was issued for (India: in.jooble.org).
    jooble_host: str = "in.jooble.org"
    # Published free-plan limits, counted locally because these APIs do not report usage.
    jsearch_monthly_limit: int = Field(default=200, ge=1)
    # Job-alert emails: a dedicated inbox read over IMAP (optional), and a folder for .eml files.
    alerts_imap_host: str | None = None
    alerts_imap_user: str | None = None
    alerts_imap_app_password: str | None = None
    alerts_imap_folder: str = "INBOX"
    alerts_lookback_days: int = Field(default=14, ge=1, le=90)
    alerts_max_messages: int = Field(default=200, ge=1, le=2000)
    alerts_dropbox_dir: str = ""   # empty: data/alert_dropbox
    # The Radar sync's one daily JSearch request: empty query = saved roles joined with OR.
    jsearch_daily_query: str = ""
    jsearch_daily_date_posted: str = Field(default="3days", pattern=r"^(?:all|today|3days|week|month)$")
    adzuna_daily_limit: int = Field(default=250, ge=1)
    adzuna_monthly_limit: int = Field(default=2500, ge=1)
    jooble_key_limit: int = Field(default=500, ge=1)
    # Comma-separated; providers without credentials are skipped.
    job_providers: str = "radar,jsearch,adzuna,jooble"
    search_country: str = "in"
    verification_concurrency: int = 4
    # Aggregator results are reused for this many hours (0 disables the cache).
    search_cache_hours: float = Field(default=12.0, ge=0, le=168)
    # Ask before a dashboard search that will send at least this many provider requests.
    search_warn_requests: int = Field(default=5, ge=1, le=50)

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
    # Pre-M1B location of the seen-job history; copied once into data_dir. Empty disables.
    legacy_history_path: str = str(BASE_DIR / "app" / "storage" / "job_history.sqlite3")

    @field_validator("jooble_host")
    @classmethod
    def _jooble_site(cls, value: str) -> str:
        # The API key is sent in the URL path, so only Jooble's own country sites are allowed.
        value = value.strip().lower()
        if not re.fullmatch(r"(?:[a-z]{2}\.)?jooble\.org", value):
            raise ValueError("JOOBLE_HOST must be a Jooble country site such as in.jooble.org")
        return value

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

    # Hosted demo: synthetic data only, isolated from real user state (see _demo_isolation).
    demo_mode: bool = False
    demo_data_dir: str = str(Path(tempfile.gettempdir()) / "career-agent-demo")
    demo_ignored: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _demo_isolation(self):
        """In DEMO_MODE nothing can reach real data, credentials or outside services."""
        if not self.demo_mode:
            return self
        demo = Path(self.demo_data_dir).resolve()
        for real in {Path(self.data_dir).resolve(), (BASE_DIR / "data").resolve()}:
            if demo == real or real in demo.parents or demo in real.parents:
                raise ValueError("DEMO_DATA_DIR must be separate from the real data directory")
        self.demo_ignored = [name for name in DEMO_BLANKED if getattr(self, name)]
        for name in DEMO_BLANKED:
            setattr(self, name, None)
        self.data_dir = str(demo)
        self.upload_dir = str(demo / "uploads")
        self.langgraph_checkpoint_path = str(demo / "langgraph.sqlite3")
        self.legacy_history_path = ""
        self.job_providers = "demo"
        self.chat_model_provider = "none"
        return self

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
