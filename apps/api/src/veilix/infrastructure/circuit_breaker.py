"""Circuit breaker for the search backend.

Exactly one breaker exists, around SearXNG as a whole. Per-engine breaking is
upstream's job and it already does it better than we could — with error-typed
back-off, and with visibility into individual engine calls that we do not have
(ADR-0006).

Scope, stated honestly: this breaker is **per process**. With one API replica
that is the whole system. With several, each maintains its own view and the
effective failure threshold multiplies by the replica count. Sharing state
through Valkey would fix that, at the cost of a network round trip on the path
that is already failing — which is the wrong place to add a dependency. The
single-replica assumption is recorded rather than papered over.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TypeVar

from veilix.core.errors import CircuitOpenError
from veilix.core.logging import get_logger
from veilix.core.telemetry import breaker_state, breaker_transitions_total

log = get_logger(__name__)

T = TypeVar("T")


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_STATE_VALUE = {BreakerState.CLOSED: 0.0, BreakerState.HALF_OPEN: 1.0, BreakerState.OPEN: 2.0}


class CircuitBreaker:
    """Trips after consecutive failures; probes once after a cooldown.

    Consecutive rather than windowed failure counting is chosen on purpose: an
    instance that is fundamentally unreachable fails every call in a row and
    trips quickly, while an instance that intermittently drops one call in ten
    keeps serving. A ratio-based breaker would trip on the second case, which
    is exactly when partial service is still worth having.
    """

    def __init__(
        self,
        *,
        name: str = "searxng",
        fail_threshold: int = 5,
        reset_timeout_s: float = 30.0,
    ) -> None:
        self._name = name
        self._fail_threshold = fail_threshold
        self._reset_timeout_s = reset_timeout_s

        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        # Guards the state transitions below.
        self._lock = asyncio.Lock()
        # True while a half-open probe is in flight.
        #
        # The lock alone is not enough, and this is worth being explicit about
        # because the gap is easy to miss and a test caught it: the lock is
        # released before `fn` is awaited, so without this flag every request
        # that arrived during the cooldown would observe state HALF_OPEN, find
        # nothing blocking it, and be admitted. Ten concurrent requests would
        # send ten probes into a dependency that is still recovering — exactly
        # the stampede the breaker exists to prevent.
        self._probe_in_flight = False

        breaker_state.labels(dependency=self._name).set(_STATE_VALUE[BreakerState.CLOSED])

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def retry_after_s(self) -> int:
        """Seconds until the next probe is allowed, for a Retry-After header."""
        if self._state is not BreakerState.OPEN:
            return 0
        remaining = self._reset_timeout_s - (time.monotonic() - self._opened_at)
        return max(1, int(remaining))

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run ``fn`` under the breaker.

        Raises ``CircuitOpenError`` without invoking ``fn`` while open. That
        distinction matters: shedding load is a decision Veilix made, not a
        failure the upstream reported, and the error taxonomy keeps the two
        apart so a dashboard can tell them apart too.
        """
        await self._before_call()
        try:
            result = await fn()
        except Exception:
            await self._on_failure()
            raise
        await self._on_success()
        return result

    async def _before_call(self) -> None:
        async with self._lock:
            if self._state is BreakerState.OPEN:
                if time.monotonic() - self._opened_at < self._reset_timeout_s:
                    raise CircuitOpenError(retry_after=self.retry_after_s)
                # Cooldown elapsed: admit exactly one probe.
                self._transition(BreakerState.HALF_OPEN)
                self._probe_in_flight = True
                return

            if self._state is BreakerState.HALF_OPEN:
                if self._probe_in_flight:
                    # A probe is already testing the dependency. Everyone else
                    # waits for its verdict rather than joining it.
                    raise CircuitOpenError(retry_after=max(1, int(self._reset_timeout_s)))
                self._probe_in_flight = True

    async def _on_success(self) -> None:
        async with self._lock:
            self._consecutive_failures = 0
            self._probe_in_flight = False
            if self._state is not BreakerState.CLOSED:
                self._transition(BreakerState.CLOSED)

    async def _on_failure(self) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            self._probe_in_flight = False

            if self._state is BreakerState.HALF_OPEN:
                # The probe failed: reopen immediately and restart the
                # cooldown, rather than letting more traffic through.
                self._opened_at = time.monotonic()
                self._transition(BreakerState.OPEN)
                return

            if self._consecutive_failures >= self._fail_threshold:
                self._opened_at = time.monotonic()
                self._transition(BreakerState.OPEN)

    def _transition(self, new_state: BreakerState) -> None:
        if new_state is self._state:
            return
        previous = self._state
        self._state = new_state

        breaker_state.labels(dependency=self._name).set(_STATE_VALUE[new_state])
        breaker_transitions_total.labels(dependency=self._name, to_state=new_state.value).inc()
        log.warning(
            "circuit_breaker_transition",
            dependency=self._name,
            previous=previous.value,
            current=new_state.value,
            consecutive_failures=self._consecutive_failures,
        )
