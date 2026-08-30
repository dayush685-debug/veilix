"""Application configuration.

Configuration is read once at startup and validated eagerly. Anything invalid
aborts the process rather than surfacing as a confusing runtime failure three
layers deep, and production has stricter rules than development because the
cost of a mistake is different.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "testing", "production"]

# Upstream SearXNG ships this literal value. Starting with it in place means
# every signed URL and session cookie is forgeable by anyone reading the docs.
SEARXNG_PLACEHOLDER_SECRET = "ultrasecretkey"  # noqa: S105 - detected, never used

_MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Validated application settings.

    Field names map to ``VEILIX_``-prefixed environment variables.
    """

    model_config = SettingsConfigDict(
        env_prefix="VEILIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # -- Deployment ---------------------------------------------------------

    env: Environment = "development"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # -- Upstreams ----------------------------------------------------------

    searxng_url: HttpUrl = Field(default=HttpUrl("http://searxng:8080"))
    valkey_url: str = Field(default="valkey://valkey:6379/0")

    # -- Secrets ------------------------------------------------------------

    # Shared with the SearXNG container. Needed to sign image-proxy URLs so
    # thumbnails are fetched server-side rather than by the user's browser
    # (docs/privacy.md §6). Read without the VEILIX_ prefix because the same
    # variable configures the SearXNG service.
    searxng_secret: str = Field(default="", validation_alias="SEARXNG_SECRET")

    # Seed for the rate limiter's daily-rotating salt (ADR-0003). Empty in
    # development means an ephemeral random seed is generated per process.
    ratelimit_salt_seed: str = ""

    admin_username: str = "admin"
    admin_password_hash: str = ""

    # SHA-256 hex digests of issued API keys, comma separated. See
    # core/security.py for why these are not Argon2.
    api_key_hashes: str = ""

    # -- Search defaults ----------------------------------------------------

    default_safesearch: Annotated[int, Field(ge=0, le=2)] = 1
    default_language: str = "auto"
    max_page: Annotated[int, Field(ge=1, le=50)] = 10
    search_timeout_s: Annotated[float, Field(gt=0, le=60)] = 8.0

    # -- Cache --------------------------------------------------------------

    cache_enabled: bool = True
    cache_ttl_s: Annotated[int, Field(ge=0, le=3600)] = 300

    # -- Rate limiting ------------------------------------------------------

    ratelimit_enabled: bool = True
    ratelimit_requests: Annotated[int, Field(ge=1)] = 60
    ratelimit_window_s: Annotated[int, Field(ge=1, le=3600)] = 60
    ratelimit_apikey_requests: Annotated[int, Field(ge=1)] = 600

    # -- Resilience ---------------------------------------------------------

    breaker_fail_threshold: Annotated[int, Field(ge=1)] = 5
    breaker_reset_timeout_s: Annotated[float, Field(gt=0)] = 30.0
    search_max_retries: Annotated[int, Field(ge=0, le=3)] = 1

    # -- Observability ------------------------------------------------------

    metrics_enabled: bool = True
    otlp_endpoint: str = ""

    # -- CORS ---------------------------------------------------------------

    cors_origins: str = ""

    # ----------------------------------------------------------------------
    # Derived values
    # ----------------------------------------------------------------------

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def api_key_digests(self) -> frozenset[str]:
        """Configured API key digests, lowercased for comparison."""
        return frozenset(d.strip().lower() for d in self.api_key_hashes.split(",") if d.strip())

    @property
    def redis_url(self) -> str:
        """Valkey URL in the scheme redis-py understands.

        Valkey speaks the Redis wire protocol, but redis-py rejects the
        ``valkey://`` scheme it has never heard of. Configuration keeps the
        accurate scheme name; the client gets the one it parses.
        """
        if self.valkey_url.startswith("valkey://"):
            return "redis://" + self.valkey_url[len("valkey://") :]
        if self.valkey_url.startswith("valkeys://"):
            return "rediss://" + self.valkey_url[len("valkeys://") :]
        return self.valkey_url

    # ----------------------------------------------------------------------
    # Validation
    # ----------------------------------------------------------------------

    @field_validator("searxng_secret")
    @classmethod
    def _reject_placeholder_secret(cls, v: str) -> str:
        if v and v.strip() == SEARXNG_PLACEHOLDER_SECRET:
            raise ValueError(
                "SEARXNG_SECRET is still upstream's placeholder "
                f"{SEARXNG_PLACEHOLDER_SECRET!r}. Generate one with: "
                "openssl rand -hex 32"
            )
        return v

    @field_validator("api_key_hashes")
    @classmethod
    def _validate_key_digests(cls, v: str) -> str:
        for digest in (d.strip() for d in v.split(",") if d.strip()):
            if len(digest) != 64 or not all(c in "0123456789abcdefABCDEF" for c in digest):
                raise ValueError(
                    "VEILIX_API_KEY_HASHES must be comma-separated SHA-256 hex "
                    f"digests (64 hex chars); got a {len(digest)}-character value. "
                    "Generate with: python scripts/hash_secret.py --api-key"
                )
        return v

    @model_validator(mode="after")
    def _production_requires_real_secrets(self) -> Settings:
        """Refuse to start a production instance in an insecure state.

        Every one of these is a configuration mistake that would otherwise
        produce a system that looks healthy and is not.
        """
        if not self.is_production:
            return self

        problems: list[str] = []

        if len(self.searxng_secret) < _MIN_SECRET_LENGTH:
            problems.append(f"SEARXNG_SECRET must be at least {_MIN_SECRET_LENGTH} characters")
        if len(self.ratelimit_salt_seed) < _MIN_SECRET_LENGTH:
            problems.append(
                f"VEILIX_RATELIMIT_SALT_SEED must be at least {_MIN_SECRET_LENGTH} "
                "characters. Without a stable seed, rate-limit buckets reset on "
                "every restart and on every replica"
            )
        if not self.admin_password_hash:
            problems.append(
                "VEILIX_ADMIN_PASSWORD_HASH is required; the admin dashboard "
                "must not be reachable without authentication"
            )
        if not self.ratelimit_enabled:
            problems.append(
                "VEILIX_RATELIMIT_ENABLED=false leaves a public instance open to "
                "abuse, which gets the instance banned by upstream engines"
            )
        if "*" in self.cors_origin_list:
            problems.append("VEILIX_CORS_ORIGINS must not be '*' in production")

        if problems:
            raise ValueError(
                "Refusing to start in production with invalid configuration:\n  - "
                + "\n  - ".join(problems)
            )
        return self

    def effective_salt_seed(self) -> str:
        """Seed for the rate limiter's daily salt derivation.

        Outside production an unset seed yields a random per-process value.
        That is the privacy-preserving default: buckets do not survive a
        restart, so nothing is retained across runs. Production requires an
        explicit seed, because otherwise every replica would derive a different
        salt and each would enforce its own separate limit.
        """
        return self.ratelimit_salt_seed or secrets.token_hex(32)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so that validation runs once and every caller observes the same
    frozen object. Tests clear the cache via ``get_settings.cache_clear()``.
    """
    return Settings()
