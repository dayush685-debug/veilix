"""Configuration validation.

These tests guard the fail-fast safety net. If this validation breaks, the
failure mode is the worst kind available: a production instance starts
successfully, serves traffic, reports healthy, and is insecure, with a
forgeable SearXNG secret, an open admin endpoint, or no rate limiting.

Nothing else in the system detects that state, so it is worth testing the
guard itself carefully.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from veilix.core.config import SEARXNG_PLACEHOLDER_SECRET, Settings

VALID_SECRET = "a" * 64
VALID_SALT = "b" * 64
VALID_ARGON2 = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$hash"


def production(**overrides: object) -> dict[str, object]:
    """A production configuration that passes, with fields overridable."""
    base: dict[str, object] = {
        "env": "production",
        "searxng_secret": VALID_SECRET,
        "ratelimit_salt_seed": VALID_SALT,
        "admin_password_hash": VALID_ARGON2,
        "ratelimit_enabled": True,
        "cors_origins": "",
    }
    base.update(overrides)
    return base


class TestProductionValidation:
    def test_a_complete_production_config_is_accepted(self) -> None:
        settings = Settings(**production())  # type: ignore[arg-type]
        assert settings.is_production

    def test_rejects_a_short_searxng_secret(self) -> None:
        with pytest.raises(ValidationError, match="SEARXNG_SECRET"):
            Settings(**production(searxng_secret="tooshort"))  # type: ignore[arg-type]

    def test_rejects_a_missing_admin_password_hash(self) -> None:
        # Fail closed. "We forgot to configure admin auth" must not read as
        # "the admin endpoint is open".
        with pytest.raises(ValidationError, match="ADMIN_PASSWORD_HASH"):
            Settings(**production(admin_password_hash=""))  # type: ignore[arg-type]

    def test_rejects_a_short_ratelimit_salt_seed(self) -> None:
        # Without a stable seed, every replica derives a different salt and
        # each enforces its own separate limit for the same client.
        with pytest.raises(ValidationError, match="RATELIMIT_SALT_SEED"):
            Settings(**production(ratelimit_salt_seed="short"))  # type: ignore[arg-type]

    def test_rejects_rate_limiting_disabled_in_production(self) -> None:
        # An unlimited public instance is scraped into an upstream ban within
        # days: the instance dies without anything being breached.
        with pytest.raises(ValidationError, match="RATELIMIT_ENABLED"):
            Settings(**production(ratelimit_enabled=False))  # type: ignore[arg-type]

    def test_rejects_wildcard_cors_in_production(self) -> None:
        with pytest.raises(ValidationError, match="CORS_ORIGINS"):
            Settings(**production(cors_origins="*"))  # type: ignore[arg-type]

    def test_reports_every_problem_at_once(self) -> None:
        """All failures in one message, not one per restart.

        Surfacing them one at a time turns a misconfigured deploy into a
        guessing game: fix, redeploy, discover the next one.
        """
        with pytest.raises(ValidationError) as exc:
            Settings(  # type: ignore[arg-type]
                **production(
                    searxng_secret="short",
                    admin_password_hash="",
                    ratelimit_enabled=False,
                )
            )
        message = str(exc.value)
        assert "SEARXNG_SECRET" in message
        assert "ADMIN_PASSWORD_HASH" in message
        assert "RATELIMIT_ENABLED" in message


class TestPlaceholderSecret:
    def test_rejects_upstreams_placeholder_secret(self) -> None:
        # Upstream ships "ultrasecretkey". Leaving it in place means every
        # signed image-proxy URL is forgeable by anyone who reads the docs.
        with pytest.raises(ValidationError, match="placeholder"):
            Settings(env="development", searxng_secret=SEARXNG_PLACEHOLDER_SECRET)

    def test_rejects_the_placeholder_even_in_development(self) -> None:
        # Development instances get exposed. The check is unconditional.
        with pytest.raises(ValidationError):
            Settings(env="development", searxng_secret=f"  {SEARXNG_PLACEHOLDER_SECRET}  ")


class TestApiKeyDigestValidation:
    def test_accepts_valid_sha256_digests(self) -> None:
        settings = Settings(env="development", api_key_hashes=f"{'a' * 64},{'B' * 64}")
        assert len(settings.api_key_digests) == 2
        # Lowercased so comparison is case-insensitive on the operator's side.
        assert all(d.islower() for d in settings.api_key_digests)

    @pytest.mark.parametrize(
        "value",
        ["tooshort", "z" * 64, "a" * 63, f"{'a' * 64},nothex"],
    )
    def test_rejects_malformed_digests(self, value: str) -> None:
        # A malformed digest would silently never match, so every request with
        # a valid key would be rejected, an outage that looks like an auth bug.
        with pytest.raises(ValidationError, match="SHA-256"):
            Settings(env="development", api_key_hashes=value)

    def test_empty_means_no_keys_configured(self) -> None:
        assert Settings(env="development", api_key_hashes="").api_key_digests == frozenset()


class TestDerivedValues:
    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            ("valkey://valkey:6379/0", "redis://valkey:6379/0"),
            ("valkeys://valkey:6380/1", "rediss://valkey:6380/1"),
            ("redis://other:6379/0", "redis://other:6379/0"),
        ],
    )
    def test_valkey_url_is_translated_for_the_redis_client(
        self, configured: str, expected: str
    ) -> None:
        """Configuration keeps the accurate scheme; the client gets one it parses.

        Valkey speaks the Redis wire protocol, but redis-py rejects a
        `valkey://` scheme it has never heard of.
        """
        assert Settings(env="development", valkey_url=configured).redis_url == expected

    def test_cors_origins_are_split_and_stripped(self) -> None:
        settings = Settings(
            env="development", cors_origins=" https://a.example , https://b.example ,"
        )
        assert settings.cors_origin_list == ["https://a.example", "https://b.example"]

    def test_salt_seed_falls_back_to_a_random_value_outside_production(self) -> None:
        """An unset seed yields a per-process random value.

        That is the privacy-preserving default: buckets do not survive a
        restart, so nothing is retained across runs. Production requires an
        explicit seed for the multi-replica reason above.
        """
        settings = Settings(env="development", ratelimit_salt_seed="")
        first, second = settings.effective_salt_seed(), settings.effective_salt_seed()
        assert len(first) == 64
        # Regenerated each call, so it cannot be relied on as stable.
        assert first != second

    def test_a_configured_seed_is_returned_unchanged(self) -> None:
        settings = Settings(env="development", ratelimit_salt_seed=VALID_SALT)
        assert settings.effective_salt_seed() == VALID_SALT
        assert settings.effective_salt_seed() == VALID_SALT


class TestBounds:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("default_safesearch", 3),
            ("default_safesearch", -1),
            ("max_page", 0),
            ("max_page", 51),
            ("search_timeout_s", 0),
            ("search_timeout_s", 61),
            ("cache_ttl_s", -1),
            ("cache_ttl_s", 3601),
            ("ratelimit_requests", 0),
            ("ratelimit_window_s", 0),
            ("search_max_retries", 4),
        ],
    )
    def test_out_of_range_values_are_rejected(self, field: str, value: object) -> None:
        # Bounds are not decoration: an unbounded max_page or timeout is a
        # free amplification lever against upstream engines.
        with pytest.raises(ValidationError):
            Settings(env="development", **{field: value})  # type: ignore[arg-type]

    def test_unknown_variables_are_ignored_not_rejected(self) -> None:
        """Deliberately permissive, unlike the API's request validation.

        A container's environment is full of variables belonging to other
        things. PATH, HOSTNAME, and whatever the platform injects. Rejecting
        unknown keys here would make the service refuse to start for reasons
        that have nothing to do with it.
        """
        settings = Settings(env="development", SOME_UNRELATED_VARIABLE="x")  # type: ignore[call-arg]
        assert settings.env == "development"
