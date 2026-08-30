"""Credential handling and client identification."""

from __future__ import annotations

import pytest

from veilix.core.security import (
    client_ip_from_headers,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_admin,
    verify_api_key,
    verify_password,
)


class TestApiKeys:
    def test_generated_keys_are_unique_and_prefixed(self) -> None:
        keys = {generate_api_key() for _ in range(200)}
        assert len(keys) == 200, "generated keys must not collide"
        assert all(k.startswith("vlx_") for k in keys)

    def test_generated_keys_carry_enough_entropy(self) -> None:
        # 32 random bytes base64url-encoded is ~43 characters. If this shrinks,
        # the SHA-256 storage decision (rather than a slow hash) stops being
        # safe, because a short key becomes brute-forceable.
        assert len(generate_api_key()) >= 40

    def test_verifies_correct_key(self) -> None:
        key = generate_api_key()
        assert verify_api_key(key, frozenset({hash_api_key(key)})) is True

    def test_rejects_wrong_key(self) -> None:
        allowed = frozenset({hash_api_key(generate_api_key())})
        assert verify_api_key(generate_api_key(), allowed) is False

    def test_finds_key_among_several(self) -> None:
        keys = [generate_api_key() for _ in range(5)]
        digests = frozenset(hash_api_key(k) for k in keys)
        assert all(verify_api_key(k, digests) for k in keys)

    @pytest.mark.parametrize("presented", ["", "   ", "vlx_bogus"])
    def test_rejects_when_no_keys_configured(self, presented: str) -> None:
        # An unconfigured instance must fail closed. "We forgot to set this up"
        # must never read as "everyone is welcome".
        assert verify_api_key(presented, frozenset()) is False

    def test_digest_is_stable_and_hex(self) -> None:
        digest = hash_api_key("some-key")
        assert digest == hash_api_key("some-key")
        assert len(digest) == 64
        assert int(digest, 16) >= 0


class TestPasswords:
    def test_roundtrip(self) -> None:
        stored = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", stored) is True
        assert verify_password("wrong", stored) is False

    def test_hash_is_salted(self) -> None:
        # Two hashes of one password must differ, or an attacker who sees the
        # store learns which accounts share a password.
        assert hash_password("same") != hash_password("same")

    def test_malformed_hash_denies_rather_than_raising(self) -> None:
        # A corrupted configuration value must behave as "no access", not as a
        # 500 an attacker can use to probe configuration state.
        assert verify_password("anything", "not-an-argon2-hash") is False

    def test_empty_stored_hash_denies(self) -> None:
        assert verify_password("anything", "") is False


class TestAdminVerification:
    @pytest.fixture
    def stored(self) -> str:
        return hash_password("admin-password-12345")

    def test_accepts_correct_pair(self, stored: str) -> None:
        assert verify_admin(
            "admin",
            "admin-password-12345",
            expected_username="admin",
            password_hash=stored,
        )

    def test_rejects_wrong_username(self, stored: str) -> None:
        assert not verify_admin(
            "root",
            "admin-password-12345",
            expected_username="admin",
            password_hash=stored,
        )

    def test_rejects_wrong_password(self, stored: str) -> None:
        assert not verify_admin("admin", "nope", expected_username="admin", password_hash=stored)


class TestClientIpExtraction:
    """The header is attacker-controlled unless a trusted proxy set it.

    Getting this wrong does not break anything visibly: the limiter keeps
    running and keeps reporting numbers, while an attacker picks a fresh
    bucket per request and is never limited.
    """

    def test_ignores_forwarded_header_when_proxy_not_trusted(self) -> None:
        assert (
            client_ip_from_headers(
                forwarded_for="1.2.3.4",
                peer_ip="10.0.0.5",
                trust_proxy=False,
            )
            == "10.0.0.5"
        )

    def test_uses_forwarded_header_when_proxy_trusted(self) -> None:
        assert (
            client_ip_from_headers(
                forwarded_for="1.2.3.4",
                peer_ip="10.0.0.5",
                trust_proxy=True,
            )
            == "1.2.3.4"
        )

    def test_takes_first_entry_of_the_chain(self) -> None:
        # The first entry is the original client as appended by our own proxy.
        # Taking the last is a common inversion that trusts whatever the
        # attacker appended.
        assert (
            client_ip_from_headers(
                forwarded_for="203.0.113.9, 70.41.3.18, 150.172.238.178",
                peer_ip="10.0.0.5",
                trust_proxy=True,
            )
            == "203.0.113.9"
        )

    def test_falls_back_when_forwarded_value_is_not_an_ip(self) -> None:
        # A garbage header must not become a bucket key of its own.
        assert (
            client_ip_from_headers(
                forwarded_for="not-an-ip-address",
                peer_ip="10.0.0.5",
                trust_proxy=True,
            )
            == "10.0.0.5"
        )

    def test_unknown_when_nothing_available(self) -> None:
        # Shares one heavily-used bucket rather than bypassing the limiter
        # through an error path.
        assert (
            client_ip_from_headers(forwarded_for=None, peer_ip=None, trust_proxy=True) == "unknown"
        )

    def test_accepts_ipv6(self) -> None:
        assert (
            client_ip_from_headers(forwarded_for="2001:db8::1", peer_ip=None, trust_proxy=True)
            == "2001:db8::1"
        )
