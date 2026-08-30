"""Circuit breaker state machine (ADR-0006)."""

from __future__ import annotations

import asyncio

import pytest

from veilix.core.errors import CircuitOpenError, UpstreamUnavailableError
from veilix.infrastructure.circuit_breaker import BreakerState, CircuitBreaker


@pytest.fixture
def breaker() -> CircuitBreaker:
    return CircuitBreaker(name="test", fail_threshold=3, reset_timeout_s=0.2)


async def _fail() -> None:
    raise UpstreamUnavailableError


async def _succeed() -> str:
    return "ok"


class TestClosedState:
    async def test_starts_closed(self, breaker: CircuitBreaker) -> None:
        assert breaker.state is BreakerState.CLOSED

    async def test_passes_results_through(self, breaker: CircuitBreaker) -> None:
        assert await breaker.call(_succeed) == "ok"

    async def test_stays_closed_below_threshold(self, breaker: CircuitBreaker) -> None:
        for _ in range(2):
            with pytest.raises(UpstreamUnavailableError):
                await breaker.call(_fail)
        assert breaker.state is BreakerState.CLOSED

    async def test_success_resets_the_failure_count(self, breaker: CircuitBreaker) -> None:
        """Consecutive, not cumulative.

        An instance dropping one call in ten keeps serving; only sustained
        failure trips the breaker. A ratio-based breaker would open exactly
        when partial service is still worth having.
        """
        for _ in range(2):
            with pytest.raises(UpstreamUnavailableError):
                await breaker.call(_fail)

        await breaker.call(_succeed)

        for _ in range(2):
            with pytest.raises(UpstreamUnavailableError):
                await breaker.call(_fail)
        assert breaker.state is BreakerState.CLOSED


class TestOpenState:
    async def _trip(self, breaker: CircuitBreaker) -> None:
        for _ in range(3):
            with pytest.raises(UpstreamUnavailableError):
                await breaker.call(_fail)

    async def test_opens_at_threshold(self, breaker: CircuitBreaker) -> None:
        await self._trip(breaker)
        assert breaker.state is BreakerState.OPEN

    async def test_refuses_without_calling_upstream(self, breaker: CircuitBreaker) -> None:
        await self._trip(breaker)

        called = False

        async def tracked() -> str:
            nonlocal called
            called = True
            return "should not run"

        with pytest.raises(CircuitOpenError):
            await breaker.call(tracked)

        assert called is False, "an open breaker must not touch the dependency"

    async def test_reports_retry_after(self, breaker: CircuitBreaker) -> None:
        await self._trip(breaker)
        assert breaker.retry_after_s >= 1

    async def test_circuit_open_is_distinct_from_upstream_unavailable(
        self, breaker: CircuitBreaker
    ) -> None:
        # Load shedding is a decision Veilix made, not a failure the upstream
        # reported. Conflating them would hide the difference on a dashboard.
        await self._trip(breaker)
        with pytest.raises(CircuitOpenError) as exc:
            await breaker.call(_succeed)
        assert exc.value.status_code == 503
        assert exc.value.problem_type == "circuit-open"


class TestRecovery:
    async def _trip(self, breaker: CircuitBreaker) -> None:
        for _ in range(3):
            with pytest.raises(UpstreamUnavailableError):
                await breaker.call(_fail)

    async def test_probes_after_cooldown_and_closes_on_success(
        self, breaker: CircuitBreaker
    ) -> None:
        await self._trip(breaker)
        await asyncio.sleep(0.25)

        assert await breaker.call(_succeed) == "ok"
        assert breaker.state is BreakerState.CLOSED

    async def test_reopens_immediately_when_the_probe_fails(self, breaker: CircuitBreaker) -> None:
        await self._trip(breaker)
        await asyncio.sleep(0.25)

        with pytest.raises(UpstreamUnavailableError):
            await breaker.call(_fail)

        # Must not admit a flood after one failed probe.
        assert breaker.state is BreakerState.OPEN
        with pytest.raises(CircuitOpenError):
            await breaker.call(_succeed)

    async def test_concurrent_requests_do_not_all_probe(self, breaker: CircuitBreaker) -> None:
        """Only one request should be admitted as the half-open probe.

        Without the lock, every waiting request would pass the cooldown check
        simultaneously and stampede a dependency that is still recovering.
        """
        await self._trip(breaker)
        await asyncio.sleep(0.25)

        attempts = 0

        async def slow_fail() -> None:
            nonlocal attempts
            attempts += 1
            await asyncio.sleep(0.05)
            raise UpstreamUnavailableError

        results = await asyncio.gather(
            *(breaker.call(slow_fail) for _ in range(10)), return_exceptions=True
        )

        assert attempts == 1, f"only one probe should reach upstream, got {attempts}"
        assert sum(isinstance(r, CircuitOpenError) for r in results) == 9
