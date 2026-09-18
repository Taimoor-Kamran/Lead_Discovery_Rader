"""Application settings, sourced from environment variables only (never from code)."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# The response fields we ask Places for. Anything not listed here is never returned, so
# widening this string is the only way to widen what we store — and it changes the SKU.
DEFAULT_PLACES_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.addressComponents,"
    "places.location,places.nationalPhoneNumber,places.internationalPhoneNumber,"
    "places.websiteUri,places.businessStatus,places.types,places.primaryType,nextPageToken"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_name: str = "lead-discovery-radar"
    environment: Literal["local", "ci", "staging", "production"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://radar:radar@localhost:5432/radar"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: SecretStr = SecretStr("change-me-in-env")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14
    refresh_cookie_name: str = "radar_refresh"
    refresh_cookie_secure: bool = True
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    job_max_attempts: int = 3
    job_backoff_base_seconds: int = 2
    job_queue_name: str = "default"
    job_queue_is_async: bool = True

    # --- Google Places (New) ---
    google_places_api_key: SecretStr = SecretStr("")
    places_field_mask: str = DEFAULT_PLACES_FIELD_MASK
    places_max_results_per_job: int = 60
    places_daily_call_cap: int = 200
    places_rps: float = 5.0
    places_content_ttl_days: int = 30

    log_level: str = "INFO"
    # NoDecode: the value is a plain comma-separated list in .env, not JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def sync_database_url(self) -> str:
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
