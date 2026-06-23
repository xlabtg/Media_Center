from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
CHART_DIR = ROOT / "deploy" / "helm" / "media-center"

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

YamlMap = dict[str, Any]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def read_values() -> YamlMap:
    return cast(
        YamlMap,
        yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8")),
    )


def test_issue_250_epic_e_keeps_one_service_matrix_across_runtime_surfaces() -> None:
    compose = read_text("infra/local/docker-compose.yml")
    values = read_values()
    service_dirs = {
        path.name
        for path in (ROOT / "services").iterdir()
        if path.is_dir() and path.name != "service-template"
    }

    assert service_dirs == set(PRODUCT_SERVICES)
    assert set(values["services"]) == set(PRODUCT_SERVICES)

    for service in PRODUCT_SERVICES:
        app_package = service.replace("-", "_") + "_app"

        assert f"  {service}:" in compose
        assert f"SERVICE_PATH: services/{service}" in compose
        assert f"media-center-{service}" in compose
        assert values["services"][service]["image"]["repository"] == (
            f"media-center-{service}"
        )
        assert (ROOT / "services" / service / app_package / "main.py").is_file()


def test_issue_250_epic_e_preserves_orchestration_contracts() -> None:
    compose = read_text("infra/local/docker-compose.yml")
    values = read_values()
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((CHART_DIR / "templates").glob("*.yaml"))
    )

    required_compose_markers = [
        "x-app-service-common: &app-service-common",
        'APP_PORT: "7700"',
        "http://localhost:7700/health",
        "read_only: true",
        "/tmp:rw,noexec,nosuid,nodev,mode=1777",
        "/app/logs:rw,noexec,nosuid,nodev,mode=0775,uid=1000,gid=1000",
        "no-new-privileges:true",
        "- ALL",
        "condition: service_healthy",
    ]
    missing_compose = [
        marker for marker in required_compose_markers if marker not in compose
    ]

    assert not missing_compose
    assert values["containerPort"] == 7700
    assert values["service"]["port"] == 7700
    assert values["probes"]["liveness"]["path"] == "/health"
    assert values["probes"]["readiness"]["path"] == "/ready"
    assert values["podSecurityContext"]["runAsNonRoot"] is True
    assert values["containerSecurityContext"]["readOnlyRootFilesystem"] is True
    assert values["containerSecurityContext"]["allowPrivilegeEscalation"] is False
    assert values["containerSecurityContext"]["capabilities"]["drop"] == ["ALL"]

    required_template_markers = [
        "kind: Deployment",
        "kind: Service",
        "kind: ServiceAccount",
        "serviceAccountToken:",
        "authentication.k8s.io",
        'resources: ["tokenreviews"]',
        'verbs: ["create"]',
        "livenessProbe:",
        "readinessProbe:",
        "mountPath: /tmp",
        "mountPath: /app/logs",
    ]
    missing_templates = [
        marker for marker in required_template_markers if marker not in templates
    ]

    assert not missing_templates


def test_issue_250_stage9_acceptance_snapshot_documents_epic_e() -> None:
    snapshot = read_text("docs/STAGE_9_ACCEPTANCE.md")

    required_markers = [
        "#250",
        "E1",
        "E2",
        "E3",
        "#247",
        "#248",
        "#249",
        "infra/local/docker-compose.yml",
        "deploy/helm/media-center",
        "services/*/*_app/main.py",
        "tests/test_local_app_compose_issue247_contract.py",
        "tests/test_helm_k8s_issue248_contract.py",
        "tests/test_stage9_epic_e_issue249_contract.py",
        "tests/test_stage9_epic_e_issue250_contract.py",
        "docker compose",
        "ServiceAccount",
        "create_base_app",
        "7700",
    ]
    missing = [marker for marker in required_markers if marker not in snapshot]

    assert not missing
