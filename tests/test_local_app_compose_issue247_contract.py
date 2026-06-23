from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRODUCT_SERVICES = (
    "activity-command-center",
    "analytics-engine",
    "api-gateway",
    "blockchain-auditor",
    "cglr",
    "contribution-ledger",
    "hitl-payout-gateway",
    "messenger-adapter",
    "neuro-agent-orchestrator",
    "notification-gateway",
    "policy-manager",
    "voice-to-chain",
    "wallet",
    "web-cabinet",
)

INFRA_HEALTHY_DEPENDENCIES = (
    "postgres",
    "redis",
    "rabbitmq",
    "chroma",
    "minio",
    "otel-collector",
)


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_247_local_compose_declares_stage9_app_services() -> None:
    compose = read_text("infra/local/docker-compose.yml")

    assert "x-app-service-common: &app-service-common" in compose
    assert "dockerfile: infra/docker/service.Dockerfile" in compose
    assert 'APP_PORT: "7700"' in compose
    assert 'expose:\n    - "7700"' in compose

    service_dirs = {
        path.name
        for path in (ROOT / "services").iterdir()
        if path.is_dir() and path.name != "service-template"
    }
    assert service_dirs == set(PRODUCT_SERVICES)

    for service in PRODUCT_SERVICES:
        block = _service_block(compose, service)

        assert "<<: *app-service-common" in block
        assert (
            f"image: ghcr.io/${{GHCR_OWNER:-xlabtg}}/"
            f"media-center-{service}:${{IMAGE_TAG:-latest}}"
        ) in block
        assert f"SERVICE_NAME: {service}" in block
        assert f"SERVICE_PATH: services/{service}" in block


def test_issue_247_app_services_use_healthcheck_on_internal_7700_port() -> None:
    compose = read_text("infra/local/docker-compose.yml")

    for marker in (
        "healthcheck:",
        "urllib.request.urlopen",
        "http://localhost:7700/health",
        "interval: 15s",
        "timeout: 5s",
        "retries: 10",
    ):
        assert marker in compose

    for service in PRODUCT_SERVICES:
        app_package = service.replace("-", "_") + "_app"
        assert (ROOT / "services" / service / app_package / "main.py").is_file(), (
            f"{service} должен иметь ASGI entrypoint для healthcheck"
        )


def test_issue_247_app_services_apply_runtime_hardening() -> None:
    compose = read_text("infra/local/docker-compose.yml")

    for marker in (
        'user: "1000:1000"',
        "read_only: true",
        "tmpfs:",
        "/tmp:rw,noexec,nosuid,nodev,mode=1777",
        "/app/logs:rw,noexec,nosuid,nodev,mode=0775,uid=1000,gid=1000",
        "security_opt:",
        "no-new-privileges:true",
        "cap_drop:",
        "- ALL",
    ):
        assert marker in compose


def test_issue_247_app_services_wait_for_infra_health() -> None:
    compose = read_text("infra/local/docker-compose.yml")

    for dependency in INFRA_HEALTHY_DEPENDENCIES:
        assert f"    {dependency}:" in compose
    assert compose.count("condition: service_healthy") >= len(
        INFRA_HEALTHY_DEPENDENCIES
    )


def test_issue_247_local_docs_and_env_template_cover_app_stack() -> None:
    env_template = read_text("infra/local/.env.local.example")
    docs = "\n".join(
        [
            read_text("infra/local/README.md"),
            read_text("docs/STAGE_9_ACCEPTANCE.md"),
        ]
    )

    for marker in (
        "IMAGE_TAG=latest",
        "APP_PORT=7700",
        "JWT_SECRET=",
        "S2S_SHARED_SECRET=",
        "ACTIVITY_COMMAND_CENTER_PORT=7701",
        "WEB_CABINET_PORT=7714",
    ):
        assert marker in env_template

    for marker in (
        "#247",
        "E1",
        "infra/local/docker-compose.yml",
        "tests/test_local_app_compose_issue247_contract.py",
        "read_only",
        "tmpfs",
        "no-new-privileges:true",
        "cap_drop",
    ):
        assert marker in docs


def _service_block(compose: str, service: str) -> str:
    lines = compose.splitlines()
    header = f"  {service}:"
    for index, line in enumerate(lines):
        if line == header:
            body: list[str] = []
            for nested in lines[index + 1 :]:
                if nested.startswith("  ") and not nested.startswith("    "):
                    break
                if nested == "volumes:":
                    break
                body.append(nested)

            return "\n".join(body)

    raise AssertionError(f"{service} не найден в docker-compose.yml")
