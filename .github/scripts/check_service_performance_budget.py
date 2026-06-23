#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from textwrap import dedent
from typing import Any

DOCKER_SIZE_PATTERN = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[kmgt]?i?b)\s*$",
    re.IGNORECASE,
)
DOCKER_SIZE_MULTIPLIERS = {
    "B": 1,
    "KB": 1_000,
    "MB": 1_000_000,
    "GB": 1_000_000_000,
    "TB": 1_000_000_000_000,
    "KIB": 1_024,
    "MIB": 1_048_576,
    "GIB": 1_073_741_824,
    "TIB": 1_099_511_627_776,
}


@dataclass(frozen=True, slots=True)
class ServiceBudget:
    name: str
    image_size_budget_bytes: int
    cold_start_budget_ms: int


@dataclass(frozen=True, slots=True)
class BudgetEvaluation:
    image_size_ok: bool
    cold_start_ok: bool

    @property
    def passed(self) -> bool:
        return self.image_size_ok and self.cold_start_ok


@dataclass(frozen=True, slots=True)
class PerformanceResult:
    service: str
    image: str
    image_size_bytes: int
    image_size_budget_bytes: int
    image_size_ok: bool
    cold_start_ms: int
    cold_start_budget_ms: int
    cold_start_ok: bool
    passed: bool
    ready_endpoint: str


class BudgetCheckError(RuntimeError):
    pass


def parse_docker_size(raw_size: str) -> int:
    match = DOCKER_SIZE_PATTERN.match(raw_size)
    if match is None:
        raise ValueError(f"Unsupported Docker image size format: {raw_size!r}")

    try:
        value = Decimal(match.group("value"))
    except InvalidOperation as exc:
        raise ValueError(f"Unsupported Docker image size value: {raw_size!r}") from exc

    unit = match.group("unit").upper()
    multiplier = DOCKER_SIZE_MULTIPLIERS[unit]
    return int(value * multiplier)


def evaluate_budget(
    budget: ServiceBudget,
    *,
    image_size_bytes: int,
    cold_start_ms: int,
) -> BudgetEvaluation:
    return BudgetEvaluation(
        image_size_ok=image_size_bytes < budget.image_size_budget_bytes,
        cold_start_ok=cold_start_ms <= budget.cold_start_budget_ms,
    )


def load_service_budget(config_path: Path, service_name: str) -> ServiceBudget:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    services = config.get("services")
    if not isinstance(services, list):
        raise BudgetCheckError("Budget config must contain a services list")

    for service in services:
        if isinstance(service, dict) and service.get("name") == service_name:
            return ServiceBudget(
                name=service_name,
                image_size_budget_bytes=_required_int(
                    service,
                    "image_size_budget_bytes",
                ),
                cold_start_budget_ms=_required_int(
                    service,
                    "cold_start_budget_ms",
                ),
            )

    raise BudgetCheckError(f"No performance budget configured for {service_name!r}")


def docker_image_size_bytes(image: str) -> int:
    output = run_command(
        ["docker", "image", "ls", image, "--format", "{{.Size}}"],
    )
    sizes = [line.strip() for line in output.splitlines() if line.strip()]
    if not sizes:
        raise BudgetCheckError(f"Docker image {image!r} was not found")
    return parse_docker_size(sizes[0])


def measure_cold_start_ms(
    *,
    service_name: str,
    image: str,
    ready_endpoint: str,
    container_port: int,
    timeout_ms: int,
) -> int:
    container_name = _container_name(service_name)
    timeout_seconds = timeout_ms / 1_000
    ready_url = f"http://127.0.0.1:{container_port}{ready_endpoint}"
    driver = _cold_start_driver(
        ready_url=ready_url,
        timeout_seconds=timeout_seconds,
    )
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--env",
        f"SERVICE_NAME={service_name}",
        "--env",
        "SERVICE_VERSION=budget-check",
        "--env",
        "APP_HOST=0.0.0.0",
        "--env",
        f"APP_PORT={container_port}",
        "--env",
        "JWT_SECRET=ci-budget-jwt-secret",
        "--env",
        "LOG_LEVEL=warning",
        "--env",
        "DATABASE_URL=",
        "--env",
        "REDIS_URL=",
        "--env",
        "RABBITMQ_URL=",
        "--env",
        "S2S_AUTH_METHOD=shared_secret",
        "--env",
        "S2S_SHARED_SECRET=ci-budget-shared-secret",
        "--env",
        "K8S_AUTH_ENABLED=false",
        "--env",
        "RF_PAYMENT_GATEWAY_ENABLED=false",
        "--env",
        "OTEL_SDK_DISABLED=true",
        image,
        "python",
        "-c",
        driver,
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    output = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"^READY_MS=(?P<ready_ms>\d+)$", output, re.MULTILINE)
    if result.returncode != 0 or match is None:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            check=False,
            capture_output=True,
            text=True,
        )
        raise BudgetCheckError(
            f"{service_name} did not return HTTP 200 from {ready_url} "
            f"within {timeout_ms} ms\n{output.strip()}"
        )
    return int(match.group("ready_ms"))


def run_budget_check(
    *,
    service_name: str,
    image: str,
    config_path: Path,
    report_dir: Path,
) -> PerformanceResult:
    budget = load_service_budget(config_path, service_name)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cold_start = config["metrics"]["cold_start"]
    ready_endpoint = str(cold_start["endpoint"])
    container_port = int(cold_start["port"])
    timeout_ms = int(cold_start["timeout_ms"])

    image_size_bytes = docker_image_size_bytes(image)
    cold_start_ms = measure_cold_start_ms(
        service_name=service_name,
        image=image,
        ready_endpoint=ready_endpoint,
        container_port=container_port,
        timeout_ms=timeout_ms,
    )
    evaluation = evaluate_budget(
        budget,
        image_size_bytes=image_size_bytes,
        cold_start_ms=cold_start_ms,
    )
    result = PerformanceResult(
        service=service_name,
        image=image,
        image_size_bytes=image_size_bytes,
        image_size_budget_bytes=budget.image_size_budget_bytes,
        image_size_ok=evaluation.image_size_ok,
        cold_start_ms=cold_start_ms,
        cold_start_budget_ms=budget.cold_start_budget_ms,
        cold_start_ok=evaluation.cold_start_ok,
        passed=evaluation.passed,
        ready_endpoint=ready_endpoint,
    )
    write_report(report_dir, result)
    append_step_summary(result)
    return result


def write_report(report_dir: Path, result: PerformanceResult) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"service-performance-{result.service}.json"
    report_path.write_text(
        json.dumps(asdict(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report_path


def append_step_summary(result: PerformanceResult) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    status = "PASS" if result.passed else "FAIL"
    markdown = (
        f"## Service performance budget: `{result.service}`\n\n"
        "| Metric | Actual | Budget | Status |\n"
        "| --- | ---: | ---: | --- |\n"
        f"| Docker image size (`docker image ls`) | "
        f"{_format_mb(result.image_size_bytes)} MB | "
        f"< {_format_mb(result.image_size_budget_bytes)} MB | "
        f"{'PASS' if result.image_size_ok else 'FAIL'} |\n"
        f"| Cold-start to `{result.ready_endpoint}` | "
        f"{result.cold_start_ms} ms | "
        f"<= {result.cold_start_budget_ms} ms | "
        f"{'PASS' if result.cold_start_ok else 'FAIL'} |\n\n"
        f"Overall status: **{status}**. Budget source: "
        "`docs/operations/service-performance-budgets.json`, ADR-0008.\n"
    )
    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write(markdown)


def run_command(command: Sequence[str]) -> str:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BudgetCheckError(
            f"Command failed: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check service Docker image size and cold-start budgets.",
    )
    parser.add_argument("--service", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("docs/operations/service-performance-budgets.json"),
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("performance-reports"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = run_budget_check(
            service_name=args.service,
            image=args.image,
            config_path=args.config,
            report_dir=args.report_dir,
        )
    except BudgetCheckError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(asdict(result), sort_keys=True))
    if not result.passed:
        return 1
    return 0


def _required_int(source: dict[str, Any], key: str) -> int:
    value = source.get(key)
    if not isinstance(value, int):
        raise BudgetCheckError(f"{key} must be an integer")
    return value


def _container_name(service_name: str) -> str:
    safe_service = re.sub(r"[^a-zA-Z0-9_.-]+", "-", service_name).strip("-")
    suffix = f"{os.getpid()}-{int(time.time() * 1_000)}"
    return f"media-center-{safe_service}-budget-{suffix}"


def _cold_start_driver(*, ready_url: str, timeout_seconds: float) -> str:
    return dedent(f"""
        import subprocess
        import sys
        import time
        import urllib.error
        import urllib.request

        ready_url = {ready_url!r}
        timeout_seconds = {timeout_seconds!r}
        start = time.perf_counter()
        process = subprocess.Popen(["/app/entrypoint.sh", "serve"])
        try:
            deadline = start + timeout_seconds
            while time.perf_counter() < deadline:
                return_code = process.poll()
                if return_code is not None:
                    print(f"SERVER_EXITED={{return_code}}", file=sys.stderr)
                    sys.exit(2)
                try:
                    with urllib.request.urlopen(
                        ready_url,
                        timeout=0.2,
                    ) as response:
                        response.read()
                        if response.status == 200:
                            elapsed_ms = int((time.perf_counter() - start) * 1000)
                            print(f"READY_MS={{elapsed_ms}}", flush=True)
                            sys.exit(0)
                except (
                    TimeoutError,
                    urllib.error.HTTPError,
                    urllib.error.URLError,
                ):
                    time.sleep(0.02)

            print(f"READY_TIMEOUT={{ready_url}}", file=sys.stderr)
            sys.exit(124)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        """)


def _format_mb(size_bytes: int) -> str:
    return f"{size_bytes / 1_000_000:.1f}"


if __name__ == "__main__":
    raise SystemExit(main())
