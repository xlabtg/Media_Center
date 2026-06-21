from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

AsyncSleeper = Callable[[float], Awaitable[None]]

DEFAULT_RETRYABLE_ERROR_CODES = frozenset(
    {
        "dependency_error",
        "dependency_timeout",
        "postgresql_timeout",
        "postgresql_unavailable",
        "rabbitmq_unavailable",
        "external_api_timeout",
        "external_api_unavailable",
        "proxy_unavailable",
    }
)


class DependencyKind(StrEnum):
    DATABASE = "database"
    MESSAGE_BROKER = "message_broker"
    EXTERNAL_API = "external_api"
    PROXY = "proxy"


class FailureMode(StrEnum):
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    DEGRADED = "degraded"


class DependencyCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"


class CircuitBreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class DependencyFailure(RuntimeError):
    """Failure raised by dependency adapters and normalized by resilience guards."""

    def __init__(
        self,
        *,
        dependency_name: str,
        dependency_kind: DependencyKind,
        failure_mode: FailureMode,
        error_code: str,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.dependency_name = _normalize_name(dependency_name, "dependency_name")
        self.dependency_kind = dependency_kind
        self.failure_mode = failure_mode
        self.error_code = _normalize_error_code(error_code)
        self.message = message
        self.retryable = retryable


type AsyncDependencyOperation[T] = Callable[[], Awaitable[T]]
type AsyncFallbackHandler[T] = Callable[[DependencyFailure], Awaitable[T]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.05
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 1.0
    retryable_error_codes: frozenset[str] = DEFAULT_RETRYABLE_ERROR_CODES

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError("max_attempts должен быть положительным")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds не должен быть отрицательным")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier должен быть не меньше 1")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError(
                "max_backoff_seconds должен быть не меньше initial_backoff_seconds"
            )

        object.__setattr__(
            self,
            "retryable_error_codes",
            frozenset(
                _normalize_error_code(code) for code in self.retryable_error_codes
            ),
        )

    def is_retryable(self, failure: DependencyFailure) -> bool:
        return failure.retryable and failure.error_code in self.retryable_error_codes

    def delay_for_attempt(self, attempt: int) -> float:
        if attempt <= 0:
            raise ValueError("attempt должен быть положительным")

        delay = self.initial_backoff_seconds * (
            self.backoff_multiplier ** (attempt - 1)
        )
        return min(delay, self.max_backoff_seconds)


@dataclass(frozen=True, slots=True)
class TimeoutBudget:
    per_attempt_seconds: float | None = 1.0
    total_seconds: float | None = 3.0

    def __post_init__(self) -> None:
        if self.per_attempt_seconds is not None and self.per_attempt_seconds <= 0:
            raise ValueError("per_attempt_seconds должен быть положительным")
        if self.total_seconds is not None and self.total_seconds <= 0:
            raise ValueError("total_seconds должен быть положительным")
        if (
            self.per_attempt_seconds is not None
            and self.total_seconds is not None
            and self.total_seconds < self.per_attempt_seconds
        ):
            raise ValueError("total_seconds должен быть не меньше per_attempt_seconds")


@dataclass(frozen=True, slots=True)
class CircuitBreakerPolicy:
    failure_threshold: int = 3
    recovery_success_threshold: int = 1
    cooldown_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.failure_threshold <= 0:
            raise ValueError("failure_threshold должен быть положительным")
        if self.recovery_success_threshold <= 0:
            raise ValueError("recovery_success_threshold должен быть положительным")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds не должен быть отрицательным")


@dataclass(frozen=True, slots=True)
class CircuitBreakerSnapshot:
    dependency_name: str
    dependency_kind: DependencyKind
    state: CircuitBreakerState
    consecutive_failures: int
    recovery_successes: int
    opened_at: float | None


@dataclass(frozen=True, slots=True)
class DependencyCallResult[T]:
    dependency_name: str
    dependency_kind: DependencyKind
    status: DependencyCallStatus
    attempts: int
    value: T
    error_code: str | None = None
    degraded_reason: str | None = None
    circuit_state: CircuitBreakerState = CircuitBreakerState.CLOSED
    recovered: bool = False

    @property
    def degraded(self) -> bool:
        return self.status is DependencyCallStatus.DEGRADED


@dataclass(slots=True)
class DependencyResilienceGuard:
    dependency_name: str
    dependency_kind: DependencyKind
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_budget: TimeoutBudget = field(default_factory=TimeoutBudget)
    circuit_breaker_policy: CircuitBreakerPolicy = field(
        default_factory=CircuitBreakerPolicy
    )
    sleeper: AsyncSleeper = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    _state: CircuitBreakerState = field(
        default=CircuitBreakerState.CLOSED,
        init=False,
    )
    _consecutive_failures: int = field(default=0, init=False)
    _recovery_successes: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.dependency_name = _normalize_name(
            self.dependency_name,
            "dependency_name",
        )

    async def execute[T](
        self,
        operation: AsyncDependencyOperation[T],
        *,
        fallback: AsyncFallbackHandler[T] | None = None,
    ) -> DependencyCallResult[T]:
        self._transition_open_circuit_if_ready()
        if self._state is CircuitBreakerState.OPEN:
            failure = self._circuit_open_failure()
            value = await self._fallback_or_raise(failure, fallback)
            return self._degraded_result(
                attempts=0,
                value=value,
                failure=failure,
                degraded_reason="circuit_open",
            )

        max_attempts = (
            1
            if self._state is CircuitBreakerState.HALF_OPEN
            else self.retry_policy.max_attempts
        )
        started_at = self.clock()
        last_failure: DependencyFailure | None = None
        attempts_used = 0

        for attempt in range(1, max_attempts + 1):
            attempts_used = attempt
            try:
                value = await self._call_with_timeout(operation)
            except TimeoutError:
                failure = self._timeout_failure()
            except DependencyFailure as error:
                failure = error
            except Exception as error:
                failure = self._unknown_failure(error)
            else:
                recovered = self._record_success()
                return DependencyCallResult(
                    dependency_name=self.dependency_name,
                    dependency_kind=self.dependency_kind,
                    status=DependencyCallStatus.SUCCEEDED,
                    attempts=attempt,
                    value=value,
                    circuit_state=self._state,
                    recovered=recovered,
                )

            last_failure = failure
            self._record_failure()
            if not self._should_retry(failure, attempt, max_attempts, started_at):
                break

            await self.sleeper(self.retry_policy.delay_for_attempt(attempt))

        assert last_failure is not None
        value = await self._fallback_or_raise(last_failure, fallback)
        return self._degraded_result(
            attempts=attempts_used,
            value=value,
            failure=last_failure,
            degraded_reason="retry_exhausted",
        )

    def snapshot(self) -> CircuitBreakerSnapshot:
        return CircuitBreakerSnapshot(
            dependency_name=self.dependency_name,
            dependency_kind=self.dependency_kind,
            state=self._state,
            consecutive_failures=self._consecutive_failures,
            recovery_successes=self._recovery_successes,
            opened_at=self._opened_at,
        )

    async def _call_with_timeout[T](
        self,
        operation: AsyncDependencyOperation[T],
    ) -> T:
        if self.timeout_budget.per_attempt_seconds is None:
            return await operation()

        return await asyncio.wait_for(
            operation(),
            timeout=self.timeout_budget.per_attempt_seconds,
        )

    def _should_retry(
        self,
        failure: DependencyFailure,
        attempt: int,
        max_attempts: int,
        started_at: float,
    ) -> bool:
        if self._state is CircuitBreakerState.OPEN:
            return False
        if attempt >= max_attempts:
            return False
        if not self.retry_policy.is_retryable(failure):
            return False

        total_seconds = self.timeout_budget.total_seconds
        if total_seconds is None:
            return True

        elapsed = self.clock() - started_at
        next_delay = self.retry_policy.delay_for_attempt(attempt)
        return elapsed + next_delay < total_seconds

    def _record_success(self) -> bool:
        if self._state is CircuitBreakerState.HALF_OPEN:
            self._recovery_successes += 1
            if (
                self._recovery_successes
                >= self.circuit_breaker_policy.recovery_success_threshold
            ):
                self._state = CircuitBreakerState.CLOSED
                self._consecutive_failures = 0
                self._recovery_successes = 0
                self._opened_at = None
                return True
            return False

        self._consecutive_failures = 0
        self._recovery_successes = 0
        return False

    def _record_failure(self) -> None:
        if self._state is CircuitBreakerState.HALF_OPEN:
            self._open_circuit()
            return

        self._consecutive_failures += 1
        if self._consecutive_failures >= self.circuit_breaker_policy.failure_threshold:
            self._open_circuit()

    def _open_circuit(self) -> None:
        self._state = CircuitBreakerState.OPEN
        self._opened_at = self.clock()
        self._recovery_successes = 0

    def _transition_open_circuit_if_ready(self) -> None:
        if self._state is not CircuitBreakerState.OPEN:
            return
        if self._opened_at is None:
            return

        if (
            self.clock() - self._opened_at
            >= self.circuit_breaker_policy.cooldown_seconds
        ):
            self._state = CircuitBreakerState.HALF_OPEN
            self._recovery_successes = 0

    def _timeout_failure(self) -> DependencyFailure:
        return DependencyFailure(
            dependency_name=self.dependency_name,
            dependency_kind=self.dependency_kind,
            failure_mode=FailureMode.TIMEOUT,
            error_code="dependency_timeout",
            message="dependency call timed out",
            retryable=True,
        )

    def _unknown_failure(self, error: Exception) -> DependencyFailure:
        return DependencyFailure(
            dependency_name=self.dependency_name,
            dependency_kind=self.dependency_kind,
            failure_mode=FailureMode.UNAVAILABLE,
            error_code="dependency_error",
            message=str(error) or error.__class__.__name__,
            retryable=True,
        )

    def _circuit_open_failure(self) -> DependencyFailure:
        return DependencyFailure(
            dependency_name=self.dependency_name,
            dependency_kind=self.dependency_kind,
            failure_mode=FailureMode.DEGRADED,
            error_code="circuit_open",
            message="dependency circuit breaker is open",
            retryable=True,
        )

    async def _fallback_or_raise[T](
        self,
        failure: DependencyFailure,
        fallback: AsyncFallbackHandler[T] | None,
    ) -> T:
        if fallback is None:
            raise failure

        return await fallback(failure)

    def _degraded_result[T](
        self,
        *,
        attempts: int,
        value: T,
        failure: DependencyFailure,
        degraded_reason: str,
    ) -> DependencyCallResult[T]:
        return DependencyCallResult(
            dependency_name=self.dependency_name,
            dependency_kind=self.dependency_kind,
            status=DependencyCallStatus.DEGRADED,
            attempts=attempts,
            value=value,
            error_code=failure.error_code,
            degraded_reason=degraded_reason,
            circuit_state=self._state,
        )


def constant_fallback[T](value: T) -> AsyncFallbackHandler[T]:
    async def fallback(_: DependencyFailure) -> T:
        return value

    return fallback


def _normalize_name(value: str, label: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError(f"{label} должен быть непустой строкой")
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{label} не должен содержать пробелы")

    return normalized


def _normalize_error_code(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "":
        raise ValueError("error_code должен быть непустой строкой")
    if any(character.isspace() for character in normalized):
        raise ValueError("error_code не должен содержать пробелы")

    return normalized
