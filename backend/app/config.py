from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["local", "test", "production"] = "local"
    app_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"
    database_url: str = "postgresql+psycopg://email_finder:email_finder@localhost:5432/email_finder"
    session_secret: SecretStr
    token_encryption_key: SecretStr
    ms_client_id: str = ""
    ms_client_secret: SecretStr | None = None
    ms_client_cert_path: str | None = None
    ms_tenant: str = "organizations"
    ms_scopes: str = "openid profile offline_access https://graph.microsoft.com/Mail.Read"
    ms_redirect_uri: str = "http://localhost:8000/api/outlook/callback"
    graph_webhook_url: str = ""
    graph_webhook_client_state_secret: SecretStr = SecretStr("")
    index_folders: str = "inbox,sentitems"
    index_lookback_days: int = Field(default=365, ge=1)
    anthropic_api_key: SecretStr | None = None
    claude_model_interpret: str = ""
    claude_model_answer: str = ""
    claude_max_retries: int = Field(default=3, ge=0, le=10)
    claude_timeout_seconds: int = Field(default=30, ge=1, le=300)
    llm_provider: Literal["anthropic", "groq"] = "anthropic"
    groq_api_key: SecretStr | None = None
    groq_models_endpoint: str = Field(
        default="https://api.groq.com/openai/v1",
        validation_alias=AliasChoices("GROQ_MODELS_ENDPOINT", "GQROQ_MODELS_ENDPOINT"),
    )
    groq_model: str = "openai/gpt-oss-120b"
    embedding_api_key: SecretStr | None = None
    embedding_base_url: str = "https://api.voyageai.com/v1"
    embedding_model: str = "voyage-4"
    embedding_dim: int = Field(default=1024, ge=1, le=2000)
    chunk_target_tokens: int = 400
    chunk_overlap_tokens: int = 40
    rrf_k: int = 60
    n_candidates: int = 20
    max_alternatives: int = Field(default=3, ge=1, le=10)
    conf_margin: float = 0.015
    conf_min_score: float = 0.01
    refine_neg_sim: float = Field(default=0.35, ge=0.0, le=1.0)
    feedback_ranking_enabled: bool = False
    feedback_history_retention_days: int = Field(default=90, ge=1, le=3650)
    feedback_tie_break_boost: float = Field(default=0.005, ge=0.0, le=0.02)
    rag_max_excerpts: int = 12
    rag_excerpt_tokens: int = 350
    rate_limit_search_per_min: int = 30
    rate_limit_ask_per_min: int = 10
    # Browsing is deliberately looser than searching: opening a folder and
    # scrolling a large mailbox issues a request per page, and the acceptance
    # target is a 5,000-message folder scrolling without hitting a wall.
    rate_limit_mail_per_min: int = Field(default=240, ge=1)
    rate_limit_commitments_per_min: int = Field(default=10, ge=1)
    rate_limit_contacts_per_min: int = Field(default=10, ge=1)
    claude_daily_call_cap: int = Field(default=100, ge=1)
    commitment_folders: str = "sentitems"
    commitment_backfill_max_emails: int = Field(default=100, ge=1)
    attachment_max_mb: int = Field(default=10, ge=1, le=50)
    sync_poll_interval_minutes: int = Field(default=5, ge=1, le=1440)
    log_level: str = "info"

    @field_validator("ms_scopes")
    @classmethod
    def read_only_mail(cls, value: str) -> str:
        if "Mail.ReadWrite" in value or "Mail.Send" in value:
            raise ValueError("only the delegated read-only Mail.Read permission is allowed")
        return value

    @model_validator(mode="after")
    def production_secrets_are_real(self) -> "Settings":
        if self.app_env == "production":
            values = [self.session_secret.get_secret_value(), self.token_encryption_key.get_secret_value()]
            if any(not value or "change-me" in value or "your-" in value for value in values):
                raise ValueError("production requires non-example SESSION_SECRET and TOKEN_ENCRYPTION_KEY")
        return self

    @property
    def folders(self) -> list[str]:
        return [name.strip() for name in self.index_folders.split(",") if name.strip()]

    @property
    def commitment_folder_names(self) -> list[str]:
        return [name.strip() for name in self.commitment_folders.split(",") if name.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
