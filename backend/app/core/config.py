"""Application settings, sourced from environment variables only (never from code)."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# RFC 7518 §3.2: an HS256 key shorter than the hash output weakens the signature. PyJWT
# warns about it; outside development we refuse to start instead.
MIN_JWT_SECRET_BYTES = 32
# Environments where a weak JWT secret is a warning rather than a refusal to start.
DEVELOPMENT_ENVIRONMENTS = frozenset({"local", "development", "ci"})

# The bot's name, versioned with the spec that introduced website fetching. A site owner
# who sees this in their logs can look it up and find a contact.
BOT_USER_AGENT_NAME = "LeadDiscoveryRadarBot/0.4"

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
    # `APP_ENV` is the name the specs use; `ENVIRONMENT` is what v0.1.0 shipped with.
    environment: Literal["local", "development", "ci", "staging", "production"] = Field(
        default="local", validation_alias=AliasChoices("ENVIRONMENT", "APP_ENV")
    )
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

    # --- Website audit (v0.4.0) ---
    # Optional: PageSpeed Insights answers without a key at a low shared quota.
    pagespeed_api_key: SecretStr = SecretStr("")
    # Shown in the bot's User-Agent so a site owner can reach a human. Polite crawling
    # identifies itself; an empty value falls back to a description, never to a lie.
    bot_contact: str = ""
    audit_max_concurrency: int = 4
    audit_max_bytes: int = 2_000_000
    audit_connect_timeout_seconds: float = 5.0
    audit_read_timeout_seconds: float = 10.0
    audit_total_timeout_seconds: float = 20.0
    audit_max_redirects: int = 3
    audit_host_throttle_seconds: float = 5.0
    audit_robots_cache_seconds: int = 60 * 60 * 24
    # How long an audit's page text may be kept, and when a stored audit is stale enough
    # for the pipeline to re-run it.
    audit_content_ttl_days: int = 90
    audit_max_age_days: int = 30
    audit_slow_mobile_score: int = 50
    audit_stale_copyright_years: int = 3
    audit_page_text_max_chars: int = 20_000
    psi_rps: float = 1.0
    psi_daily_call_cap: int = 200
    # Industries where booking or scheduling online is normal, so its absence is a
    # finding. Everywhere else `no_online_booking` would be noise, not an observation.
    audit_booking_industries: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "plumbing",
            "hvac",
            "electrical",
            "roofing",
            "general_contracting",
            "locksmith",
            "pest_control",
            "cleaning",
            "landscaping",
            "moving",
            "auto_repair",
            "dental",
            "medical",
            "chiropractic",
            "physiotherapy",
            "veterinary",
            "hair_salon",
            "beauty",
            "spa",
            "fitness",
            "legal",
            "accounting",
        ]
    )

    # --- AI classification (v0.5.0) ---
    # openai | fake | disabled. Unset → resolved from the environment and the key: see
    # `resolved_ai_provider`. The fake provider answers from `app/demo/ai/` and never
    # leaves the machine.
    ai_provider: Literal["openai", "fake", "disabled"] | None = None
    openai_api_key: SecretStr = SecretStr("")
    # Exact API model names from OpenAI's current list. Left empty, the fake provider
    # uses placeholder names and the real one refuses to start.
    ai_triage_model: str = ""
    ai_escalation_model: str = ""
    # Per-million-token prices, copied from the pricing page. Only our cost estimate uses
    # them; missing prices record `est_cost_usd = null` and the budget counts calls.
    ai_triage_price_in_per_m: float | None = None
    ai_triage_price_out_per_m: float | None = None
    ai_escalation_price_in_per_m: float | None = None
    ai_escalation_price_out_per_m: float | None = None
    ai_escalation_enabled: bool = True
    ai_daily_budget_usd: float = 2.0
    ai_max_calls_per_run: int = 200
    # The call-count guard behind the daily budget, and what the `openai` source row is
    # rate limited to. Also the only daily guard when prices are not configured.
    ai_daily_call_cap: int = 500
    ai_rps: float = 2.0
    ai_page_text_max_chars: int = 8_000
    ai_timeout_seconds: float = 60.0
    ai_max_retries: int = 2
    ai_raw_output_max_chars: int = 20_000
    # `buying_intent = explicit` survives the guardrails only when a valid evidence quote
    # contains one of these. Anything else is `none_detected`: intent is never guessed.
    ai_explicit_intent_patterns: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "looking for a new website",
            "looking for a web designer",
            "hiring a web designer",
            "need a new website",
            "want a new website",
            "need help with marketing",
            "looking for marketing help",
            "hiring a marketing agency",
        ]
    )
    # Scoring weights (`scoring-1`). Assumptions until calibrated; they add up to 1.0.
    scoring_weight_facts: float = 0.25
    scoring_weight_inference: float = 0.45
    scoring_weight_intent: float = 0.10
    scoring_weight_contactability: float = 0.20

    # --- Entity resolution (v0.3.0) ---
    resolution_auto_merge: float = 0.85
    resolution_review: float = 0.60
    resolution_weight_domain: float = 0.30
    resolution_weight_phone: float = 0.30
    resolution_weight_name: float = 0.20
    resolution_weight_address: float = 0.15
    resolution_weight_geo: float = 0.05
    # Blocking guards: how many candidates one record may be scored against, and how
    # close two names must be before a shared map cell is treated as a candidate.
    resolution_max_candidates: int = 25
    resolution_block_name_ratio: float = 80.0
    # Highest priority first. Survivorship prefers a value from an earlier source.
    resolution_source_priority: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["google_places", "demo_fixture"]
    )

    log_level: str = "INFO"
    # NoDecode: the value is a plain comma-separated list in .env, not JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator(
        "cors_origins",
        "resolution_source_priority",
        "audit_booking_industries",
        "ai_explicit_intent_patterns",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_development(self) -> bool:
        """Whether developer-only fixtures (the demo source) may be registered.

        `local` counts: it is what `.env.example` ships and what `make up` runs as.
        """
        return self.environment in {"local", "development"}

    @property
    def sync_database_url(self) -> str:
        return self.database_url

    @property
    def fixtures_allowed(self) -> bool:
        """Whether the offline fixture backends may answer at all.

        Wider than `is_development`, which gates the demo *source*: `ci` is where the test
        suite runs, and the whole point of the fixture backends is that the suite never
        touches the network. Staging and production get the network backend only.
        """
        return self.environment in DEVELOPMENT_ENVIRONMENTS

    @property
    def jwt_secret_is_strong(self) -> bool:
        return len(self.jwt_secret.get_secret_value().encode()) >= MIN_JWT_SECRET_BYTES

    @property
    def requires_strong_jwt_secret(self) -> bool:
        """Whether a weak JWT secret must stop the process rather than warn."""
        return self.environment not in DEVELOPMENT_ENVIRONMENTS

    @property
    def resolved_ai_provider(self) -> str:
        """Which LLM provider the classification step talks to.

        An explicit `AI_PROVIDER` always wins. Otherwise a configured key means the real
        provider; without one, development and CI get the fake provider (so the demo and
        the tests work offline) and every other environment skips AI entirely rather than
        pretend. Nothing here ever invents an answer.
        """
        if self.ai_provider is not None:
            return self.ai_provider
        if self.openai_api_key.get_secret_value():
            return "openai"
        return "fake" if self.fixtures_allowed else "disabled"

    @property
    def ai_enabled(self) -> bool:
        return self.resolved_ai_provider != "disabled"

    @property
    def user_agent(self) -> str:
        """What the audit fetcher calls itself. Always identifies the bot and a contact."""
        contact = self.bot_contact.strip() or "set BOT_CONTACT to reach a human"
        return f"{BOT_USER_AGENT_NAME} (+{contact})"


@lru_cache
def get_settings() -> Settings:
    return Settings()
