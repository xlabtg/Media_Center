from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from libs.shared import (
    CircuitBreakerPolicy,
    CircuitBreakerState,
    DependencyCallResult,
    DependencyCallStatus,
    DependencyFailure,
    DependencyKind,
    DependencyResilienceGuard,
    FailureMode,
    RetryPolicy,
    TimeoutBudget,
    constant_fallback,
)

ROOT = Path(__file__).resolve().parents[1]
type ChaosDependencyResult = DependencyCallResult[dict[str, str]]


def test_issue89_chaos_scenarios_cover_dependency_failures() -> None:
    results = asyncio.run(_run_issue89_dependency_failure_scenarios())

    assert {result.dependency_kind for result in results} == {
        DependencyKind.DATABASE,
        DependencyKind.MESSAGE_BROKER,
        DependencyKind.EXTERNAL_API,
        DependencyKind.PROXY,
    }
    assert [result.status for result in results] == [
        DependencyCallStatus.SUCCEEDED,
        DependencyCallStatus.DEGRADED,
        DependencyCallStatus.DEGRADED,
        DependencyCallStatus.DEGRADED,
    ]
    assert results[0].attempts == 3
    assert results[0].value == {"source": "postgres", "mode": "primary"}
    assert results[1].attempts == 1
    assert results[1].error_code == "rabbitmq_unavailable"
    assert results[1].value == {"source": "outbox", "mode": "queued"}
    assert results[2].error_code == "dependency_timeout"
    assert results[2].degraded_reason == "retry_exhausted"
    assert results[2].value == {"source": "cache", "mode": "stale"}
    assert results[3].error_code == "proxy_unavailable"
    assert results[3].value == {"source": "proxy_pool", "mode": "disabled"}


def test_issue89_circuit_breaker_recovers_after_dependency_restores() -> None:
    clock = ManualClock()
    delays: list[float] = []
    guard = DependencyResilienceGuard(
        dependency_name="postgresql",
        dependency_kind=DependencyKind.DATABASE,
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0.1),
        timeout_budget=TimeoutBudget(per_attempt_seconds=0.1, total_seconds=1),
        circuit_breaker_policy=CircuitBreakerPolicy(
            failure_threshold=2,
            recovery_success_threshold=1,
            cooldown_seconds=5,
        ),
        sleeper=_recording_sleeper(delays, clock),
        clock=clock.monotonic,
    )
    failing_operation = scripted_operation(
        [
            DependencyFailure(
                dependency_name="postgresql",
                dependency_kind=DependencyKind.DATABASE,
                failure_mode=FailureMode.UNAVAILABLE,
                error_code="postgresql_unavailable",
                message="database is unavailable",
                retryable=True,
            ),
            DependencyFailure(
                dependency_name="postgresql",
                dependency_kind=DependencyKind.DATABASE,
                failure_mode=FailureMode.UNAVAILABLE,
                error_code="postgresql_unavailable",
                message="database is unavailable",
                retryable=True,
            ),
        ]
    )

    first = asyncio.run(
        guard.execute(
            failing_operation,
            fallback=constant_fallback({"source": "cache", "mode": "readonly"}),
        )
    )
    second = asyncio.run(
        guard.execute(
            failing_operation,
            fallback=constant_fallback({"source": "cache", "mode": "readonly"}),
        )
    )
    short_circuit = asyncio.run(
        guard.execute(
            scripted_operation([{"source": "postgres", "mode": "primary"}]),
            fallback=constant_fallback({"source": "cache", "mode": "readonly"}),
        )
    )

    assert first.status is DependencyCallStatus.DEGRADED
    assert second.circuit_state is CircuitBreakerState.OPEN
    assert short_circuit.status is DependencyCallStatus.DEGRADED
    assert short_circuit.attempts == 0
    assert short_circuit.degraded_reason == "circuit_open"

    clock.advance(5)
    recovered = asyncio.run(
        guard.execute(
            scripted_operation([{"source": "postgres", "mode": "primary"}]),
            fallback=constant_fallback({"source": "cache", "mode": "readonly"}),
        )
    )

    assert recovered.status is DependencyCallStatus.SUCCEEDED
    assert recovered.recovered is True
    assert recovered.circuit_state is CircuitBreakerState.CLOSED
    assert guard.snapshot().state is CircuitBreakerState.CLOSED
    assert delays == []


def test_issue89_timeout_budget_fails_fast_and_uses_fallback() -> None:
    guard = DependencyResilienceGuard(
        dependency_name="external-kpi-api",
        dependency_kind=DependencyKind.EXTERNAL_API,
        retry_policy=RetryPolicy(max_attempts=1),
        timeout_budget=TimeoutBudget(per_attempt_seconds=0.001, total_seconds=0.01),
        sleeper=_noop_sleep,
    )

    result = asyncio.run(
        guard.execute(
            _slow_external_call,
            fallback=constant_fallback({"source": "cache", "mode": "stale"}),
        )
    )

    assert result.status is DependencyCallStatus.DEGRADED
    assert result.error_code == "dependency_timeout"
    assert result.value == {"source": "cache", "mode": "stale"}


def test_issue89_chaos_testing_contract_is_documented() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    strategy = (ROOT / "docs/TESTING_STRATEGY.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/SRE_RUNBOOK.md").read_text(encoding="utf-8")
    chaos = (ROOT / "docs/CHAOS_TESTING.md").read_text(encoding="utf-8")

    for marker in (
        "Статус: chaos-ready для issue #89",
        "tests/test_chaos_resilience_issue89_contract.py",
        "DependencyResilienceGuard",
        "RetryPolicy",
        "TimeoutBudget",
        "CircuitBreakerPolicy",
        "PostgreSQL",
        "RabbitMQ",
        "external API",
        "proxy",
        "controlled_degradation",
        "recovery_confirmed",
    ):
        assert marker in chaos

    assert "docs/CHAOS_TESTING.md" in readme
    assert "docs/CHAOS_TESTING.md" in strategy
    assert "docs/CHAOS_TESTING.md" in runbook


async def _run_issue89_dependency_failure_scenarios() -> list[ChaosDependencyResult]:
    delays: list[float] = []
    db_guard = DependencyResilienceGuard(
        dependency_name="postgresql",
        dependency_kind=DependencyKind.DATABASE,
        retry_policy=RetryPolicy(max_attempts=3, initial_backoff_seconds=0.01),
        timeout_budget=TimeoutBudget(per_attempt_seconds=0.05, total_seconds=1),
        circuit_breaker_policy=CircuitBreakerPolicy(failure_threshold=5),
        sleeper=_recording_sleeper(delays, ManualClock()),
    )
    broker_guard = DependencyResilienceGuard(
        dependency_name="rabbitmq",
        dependency_kind=DependencyKind.MESSAGE_BROKER,
        retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0.01),
        timeout_budget=TimeoutBudget(per_attempt_seconds=0.05, total_seconds=1),
        circuit_breaker_policy=CircuitBreakerPolicy(failure_threshold=1),
        sleeper=_noop_sleep,
    )
    api_guard = DependencyResilienceGuard(
        dependency_name="external-kpi-api",
        dependency_kind=DependencyKind.EXTERNAL_API,
        retry_policy=RetryPolicy(max_attempts=1),
        timeout_budget=TimeoutBudget(per_attempt_seconds=0.001, total_seconds=0.01),
        sleeper=_noop_sleep,
    )
    proxy_guard = DependencyResilienceGuard(
        dependency_name="telegram-proxy-pool",
        dependency_kind=DependencyKind.PROXY,
        retry_policy=RetryPolicy(max_attempts=1),
        timeout_budget=TimeoutBudget(per_attempt_seconds=0.05, total_seconds=1),
        circuit_breaker_policy=CircuitBreakerPolicy(failure_threshold=1),
        sleeper=_noop_sleep,
    )

    return [
        await db_guard.execute(
            scripted_operation(
                [
                    DependencyFailure(
                        dependency_name="postgresql",
                        dependency_kind=DependencyKind.DATABASE,
                        failure_mode=FailureMode.UNAVAILABLE,
                        error_code="postgresql_unavailable",
                        message="database connection refused",
                        retryable=True,
                    ),
                    DependencyFailure(
                        dependency_name="postgresql",
                        dependency_kind=DependencyKind.DATABASE,
                        failure_mode=FailureMode.TIMEOUT,
                        error_code="postgresql_timeout",
                        message="database query timed out",
                        retryable=True,
                    ),
                    {"source": "postgres", "mode": "primary"},
                ]
            ),
            fallback=constant_fallback({"source": "cache", "mode": "readonly"}),
        ),
        await broker_guard.execute(
            scripted_operation(
                [
                    DependencyFailure(
                        dependency_name="rabbitmq",
                        dependency_kind=DependencyKind.MESSAGE_BROKER,
                        failure_mode=FailureMode.UNAVAILABLE,
                        error_code="rabbitmq_unavailable",
                        message="broker is unavailable",
                        retryable=True,
                    )
                ]
            ),
            fallback=constant_fallback({"source": "outbox", "mode": "queued"}),
        ),
        await api_guard.execute(
            _slow_external_call,
            fallback=constant_fallback({"source": "cache", "mode": "stale"}),
        ),
        await proxy_guard.execute(
            scripted_operation(
                [
                    DependencyFailure(
                        dependency_name="telegram-proxy-pool",
                        dependency_kind=DependencyKind.PROXY,
                        failure_mode=FailureMode.UNAVAILABLE,
                        error_code="proxy_unavailable",
                        message="no healthy proxy leases",
                        retryable=True,
                    )
                ]
            ),
            fallback=constant_fallback({"source": "proxy_pool", "mode": "disabled"}),
        ),
    ]


class ManualClock:
    def __init__(self) -> None:
        self._value = 0.0

    def monotonic(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += seconds


async def _noop_sleep(delay: float) -> None:
    assert delay >= 0


def _recording_sleeper(
    delays: list[float],
    clock: ManualClock,
) -> Callable[[float], Awaitable[None]]:
    async def sleep(delay: float) -> None:
        delays.append(delay)
        clock.advance(delay)

    return sleep


def scripted_operation(
    outcomes: list[dict[str, str] | DependencyFailure],
) -> Callable[[], Awaitable[dict[str, str]]]:
    async def operation() -> dict[str, str]:
        if not outcomes:
            raise AssertionError("scripted_operation exhausted")
        outcome = outcomes.pop(0)
        if isinstance(outcome, DependencyFailure):
            raise outcome

        return outcome

    return operation


async def _slow_external_call() -> dict[str, str]:
    await asyncio.sleep(0.05)
    return {"source": "external-kpi-api", "mode": "primary"}
