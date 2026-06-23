from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SERVICES = (
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


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def read_workflow_bundle() -> str:
    return "\n".join(
        [
            read_text(".github/workflows/ci.yml"),
            read_text(".github/workflows/build-service.yml"),
        ]
    )


def test_ci_workflow_declares_quality_security_and_image_checks() -> None:
    workflow = read_workflow_bundle()

    required_markers = [
        "ruff check .",
        "ruff format --check .",
        "mypy .",
        "pytest",
        "pip-audit . --progress-spinner off",
        "gitleaks detect --source . --no-git",
        ".gitleaks.toml",
        "aquasecurity/trivy-action@v0.36.0",
        "docker/build-push-action@v7",
        "infra/docker/service.Dockerfile",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing


def test_ci_image_matrix_covers_all_baseline_services() -> None:
    workflow = read_text(".github/workflows/ci.yml")
    reusable = read_text(".github/workflows/build-service.yml")

    for service in EXPECTED_SERVICES:
        assert f"- {service}" in workflow
    assert "SERVICE_PATH=services/${{ inputs.service }}" in reusable


def test_service_dockerfile_uses_adr_baseline_python_image() -> None:
    dockerfile = read_text("infra/docker/service.Dockerfile")

    assert "FROM python:3.13.14-slim" in dockerfile
    assert "python:latest" not in dockerfile
    assert "ARG SERVICE_NAME" in dockerfile
    assert "ARG SERVICE_PATH" in dockerfile


def test_dev_requirements_pin_ci_tool_versions() -> None:
    requirements = set(read_text("requirements-dev.txt").splitlines())

    expected_requirements = {
        "ruff==0.15.17",
        "mypy==2.1.0",
        "pytest==9.1.0",
        "pip-audit==2.10.1",
    }

    assert expected_requirements.issubset(requirements)
