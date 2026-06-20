from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from math import ceil
from time import perf_counter_ns
from typing import Protocol


class LoadOperation(Protocol):
    def __call__(self, iteration: int) -> bool: ...


class AsyncLoadOperation(Protocol):
    def __call__(self, iteration: int) -> Awaitable[bool]: ...


@dataclass(frozen=True, slots=True)
class LoadTarget:
    name: str
    operation_count: int
    min_throughput_per_second: float | None = None
    max_p95_latency_ms: float | None = None
    min_success_ratio: float = 1.0
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("load target name не может быть пустым")
        if self.operation_count < 1:
            raise ValueError("operation_count должен быть не меньше 1")
        if (
            self.min_throughput_per_second is not None
            and self.min_throughput_per_second < 0
        ):
            raise ValueError("min_throughput_per_second не может быть отрицательным")
        if self.max_p95_latency_ms is not None and self.max_p95_latency_ms < 0:
            raise ValueError("max_p95_latency_ms не может быть отрицательным")
        if not 0 <= self.min_success_ratio <= 1:
            raise ValueError("min_success_ratio должен быть в диапазоне 0..1")


@dataclass(frozen=True, slots=True)
class LoadScenarioResult:
    name: str
    operation_count: int
    successful_operations: int
    total_duration_ms: float
    throughput_per_second: float
    success_ratio: float
    p95_latency_ms: float
    max_latency_ms: float
    failure_details: tuple[str, ...] = ()

    @property
    def failed_operations(self) -> int:
        return self.operation_count - self.successful_operations


@dataclass(frozen=True, slots=True)
class LoadTargetEvaluation:
    target: LoadTarget
    result: LoadScenarioResult
    unmet_conditions: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.unmet_conditions == ()


@dataclass(frozen=True, slots=True, init=False)
class LoadTestReport:
    evaluations: tuple[LoadTargetEvaluation, ...]

    def __init__(self, evaluations: Sequence[LoadTargetEvaluation]) -> None:
        normalized = tuple(evaluations)
        if not normalized:
            raise ValueError("load test report должен содержать хотя бы один сценарий")
        object.__setattr__(self, "evaluations", normalized)

    @property
    def passed(self) -> bool:
        return all(evaluation.passed for evaluation in self.evaluations)

    def evaluation_for(self, name: str) -> LoadTargetEvaluation:
        for evaluation in self.evaluations:
            if evaluation.target.name == name:
                return evaluation
        raise KeyError(name)

    def summary(self) -> str:
        return "\n".join(
            _format_evaluation(evaluation) for evaluation in self.evaluations
        )


def run_load_scenario(
    target: LoadTarget,
    operation: LoadOperation,
    *,
    clock_ns: Callable[[], int] = perf_counter_ns,
    max_failure_details: int = 10,
) -> LoadScenarioResult:
    latencies_ms: list[float] = []
    successful_operations = 0
    failure_details: list[str] = []
    scenario_start_ns = clock_ns()

    for iteration in range(target.operation_count):
        operation_start_ns = clock_ns()
        succeeded = False
        raised = False
        try:
            succeeded = bool(operation(iteration))
        except Exception as error:
            raised = True
            if len(failure_details) < max_failure_details:
                failure_details.append(f"{iteration}: {type(error).__name__}: {error}")
        finally:
            operation_end_ns = clock_ns()
            latencies_ms.append(_duration_ms(operation_start_ns, operation_end_ns))

        if succeeded:
            successful_operations += 1
        elif not raised and len(failure_details) < max_failure_details:
            failure_details.append(f"{iteration}: operation returned false")

    scenario_end_ns = clock_ns()
    total_duration_ns = max(scenario_end_ns - scenario_start_ns, 0)
    total_duration_seconds = max(total_duration_ns / 1_000_000_000, 1e-9)

    return LoadScenarioResult(
        name=target.name,
        operation_count=target.operation_count,
        successful_operations=successful_operations,
        total_duration_ms=total_duration_ns / 1_000_000,
        throughput_per_second=target.operation_count / total_duration_seconds,
        success_ratio=successful_operations / target.operation_count,
        p95_latency_ms=_percentile(latencies_ms, 0.95),
        max_latency_ms=max(latencies_ms, default=0),
        failure_details=tuple(failure_details),
    )


def evaluate_load_target(
    target: LoadTarget,
    result: LoadScenarioResult,
) -> LoadTargetEvaluation:
    unmet_conditions: list[str] = []

    if result.operation_count != target.operation_count:
        unmet_conditions.append(
            "operation_count "
            f"{result.operation_count} != target {target.operation_count}"
        )
    if (
        target.min_throughput_per_second is not None
        and result.throughput_per_second < target.min_throughput_per_second
    ):
        unmet_conditions.append(
            "throughput "
            f"{result.throughput_per_second:.2f}/s < "
            f"{target.min_throughput_per_second:.2f}/s"
        )
    if (
        target.max_p95_latency_ms is not None
        and result.p95_latency_ms > target.max_p95_latency_ms
    ):
        unmet_conditions.append(
            f"p95 {result.p95_latency_ms:.2f}ms > {target.max_p95_latency_ms:.2f}ms"
        )
    if result.success_ratio < target.min_success_ratio:
        unmet_conditions.append(
            f"success ratio {result.success_ratio:.4f} < {target.min_success_ratio:.4f}"
        )

    return LoadTargetEvaluation(
        target=target,
        result=result,
        unmet_conditions=tuple(unmet_conditions),
    )


def run_and_evaluate_load_scenario(
    target: LoadTarget,
    operation: LoadOperation,
    *,
    clock_ns: Callable[[], int] = perf_counter_ns,
    max_failure_details: int = 10,
) -> LoadTargetEvaluation:
    return evaluate_load_target(
        target,
        run_load_scenario(
            target,
            operation,
            clock_ns=clock_ns,
            max_failure_details=max_failure_details,
        ),
    )


async def run_async_load_scenario(
    target: LoadTarget,
    operation: AsyncLoadOperation,
    *,
    max_concurrency: int,
    clock_ns: Callable[[], int] = perf_counter_ns,
    max_failure_details: int = 10,
) -> LoadScenarioResult:
    if max_concurrency < 1:
        raise ValueError("max_concurrency должен быть не меньше 1")

    latencies_ms: list[float] = []
    successful_operations = 0
    failure_details: list[str] = []
    semaphore = asyncio.Semaphore(max_concurrency)
    scenario_start_ns = clock_ns()

    async def run_limited(iteration: int) -> tuple[int, bool, float, str | None]:
        async with semaphore:
            return await _run_timed_async_operation(iteration, operation, clock_ns)

    tasks = [
        asyncio.create_task(run_limited(iteration))
        for iteration in range(target.operation_count)
    ]
    for task in asyncio.as_completed(tasks):
        _, succeeded, latency_ms, failure_detail = await task
        latencies_ms.append(latency_ms)
        if succeeded:
            successful_operations += 1
        elif failure_detail is not None and len(failure_details) < max_failure_details:
            failure_details.append(failure_detail)

    scenario_end_ns = clock_ns()
    total_duration_ns = max(scenario_end_ns - scenario_start_ns, 0)
    total_duration_seconds = max(total_duration_ns / 1_000_000_000, 1e-9)

    return LoadScenarioResult(
        name=target.name,
        operation_count=target.operation_count,
        successful_operations=successful_operations,
        total_duration_ms=total_duration_ns / 1_000_000,
        throughput_per_second=target.operation_count / total_duration_seconds,
        success_ratio=successful_operations / target.operation_count,
        p95_latency_ms=_percentile(latencies_ms, 0.95),
        max_latency_ms=max(latencies_ms, default=0),
        failure_details=tuple(failure_details),
    )


async def run_async_and_evaluate_load_scenario(
    target: LoadTarget,
    operation: AsyncLoadOperation,
    *,
    max_concurrency: int,
    clock_ns: Callable[[], int] = perf_counter_ns,
    max_failure_details: int = 10,
) -> LoadTargetEvaluation:
    return evaluate_load_target(
        target,
        await run_async_load_scenario(
            target,
            operation,
            max_concurrency=max_concurrency,
            clock_ns=clock_ns,
            max_failure_details=max_failure_details,
        ),
    )


def run_threaded_load_scenario(
    target: LoadTarget,
    operation: LoadOperation,
    *,
    max_workers: int,
    clock_ns: Callable[[], int] = perf_counter_ns,
    max_failure_details: int = 10,
) -> LoadScenarioResult:
    if max_workers < 1:
        raise ValueError("max_workers должен быть не меньше 1")

    latencies_ms: list[float] = []
    successful_operations = 0
    failure_details: list[str] = []
    scenario_start_ns = clock_ns()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_run_timed_operation, iteration, operation, clock_ns)
            for iteration in range(target.operation_count)
        ]
        for future in as_completed(futures):
            _, succeeded, latency_ms, failure_detail = future.result()
            latencies_ms.append(latency_ms)
            if succeeded:
                successful_operations += 1
            elif (
                failure_detail is not None
                and len(failure_details) < max_failure_details
            ):
                failure_details.append(failure_detail)

    scenario_end_ns = clock_ns()
    total_duration_ns = max(scenario_end_ns - scenario_start_ns, 0)
    total_duration_seconds = max(total_duration_ns / 1_000_000_000, 1e-9)

    return LoadScenarioResult(
        name=target.name,
        operation_count=target.operation_count,
        successful_operations=successful_operations,
        total_duration_ms=total_duration_ns / 1_000_000,
        throughput_per_second=target.operation_count / total_duration_seconds,
        success_ratio=successful_operations / target.operation_count,
        p95_latency_ms=_percentile(latencies_ms, 0.95),
        max_latency_ms=max(latencies_ms, default=0),
        failure_details=tuple(failure_details),
    )


def run_threaded_and_evaluate_load_scenario(
    target: LoadTarget,
    operation: LoadOperation,
    *,
    max_workers: int,
    clock_ns: Callable[[], int] = perf_counter_ns,
    max_failure_details: int = 10,
) -> LoadTargetEvaluation:
    return evaluate_load_target(
        target,
        run_threaded_load_scenario(
            target,
            operation,
            max_workers=max_workers,
            clock_ns=clock_ns,
            max_failure_details=max_failure_details,
        ),
    )


def _run_timed_operation(
    iteration: int,
    operation: LoadOperation,
    clock_ns: Callable[[], int],
) -> tuple[int, bool, float, str | None]:
    operation_start_ns = clock_ns()
    try:
        succeeded = bool(operation(iteration))
    except Exception as error:
        operation_end_ns = clock_ns()
        return (
            iteration,
            False,
            _duration_ms(operation_start_ns, operation_end_ns),
            f"{iteration}: {type(error).__name__}: {error}",
        )

    operation_end_ns = clock_ns()
    failure_detail = None if succeeded else f"{iteration}: operation returned false"
    return (
        iteration,
        succeeded,
        _duration_ms(operation_start_ns, operation_end_ns),
        failure_detail,
    )


async def _run_timed_async_operation(
    iteration: int,
    operation: AsyncLoadOperation,
    clock_ns: Callable[[], int],
) -> tuple[int, bool, float, str | None]:
    operation_start_ns = clock_ns()
    try:
        succeeded = bool(await operation(iteration))
    except Exception as error:
        operation_end_ns = clock_ns()
        return (
            iteration,
            False,
            _duration_ms(operation_start_ns, operation_end_ns),
            f"{iteration}: {type(error).__name__}: {error}",
        )

    operation_end_ns = clock_ns()
    failure_detail = None if succeeded else f"{iteration}: operation returned false"
    return (
        iteration,
        succeeded,
        _duration_ms(operation_start_ns, operation_end_ns),
        failure_detail,
    )


def _duration_ms(start_ns: int, end_ns: int) -> float:
    return max(end_ns - start_ns, 0) / 1_000_000


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = ceil(len(ordered) * percentile) - 1
    bounded_index = min(max(index, 0), len(ordered) - 1)
    return ordered[bounded_index]


def _format_evaluation(evaluation: LoadTargetEvaluation) -> str:
    result = evaluation.result
    status = "PASS" if evaluation.passed else "FAIL"
    details = (
        f"{status} {evaluation.target.name}: "
        f"{result.successful_operations}/{result.operation_count} ok, "
        f"{result.throughput_per_second:.2f}/s, "
        f"p95={result.p95_latency_ms:.2f}ms, "
        f"success={result.success_ratio:.4f}"
    )
    if evaluation.unmet_conditions:
        details += "; unmet: " + ", ".join(evaluation.unmet_conditions)
    if result.failure_details:
        details += "; failures: " + " | ".join(result.failure_details)
    return details
