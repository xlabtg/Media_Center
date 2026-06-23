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


def read_chart_values() -> YamlMap:
    return cast(
        YamlMap,
        yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8")),
    )


def read_chart_templates() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((CHART_DIR / "templates").glob("*.yaml"))
    )


def test_issue_248_helm_chart_declares_all_product_services() -> None:
    chart = cast(
        YamlMap,
        yaml.safe_load((CHART_DIR / "Chart.yaml").read_text(encoding="utf-8")),
    )
    values = read_chart_values()
    service_dirs = {
        path.name
        for path in (ROOT / "services").iterdir()
        if path.is_dir() and path.name != "service-template"
    }

    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "media-center"
    assert service_dirs == set(PRODUCT_SERVICES)
    assert set(values["services"]) == set(PRODUCT_SERVICES)
    assert values["image"]["registry"] == "ghcr.io"
    assert values["image"]["repositoryOwner"] == "xlabtg"

    for service in PRODUCT_SERVICES:
        service_values = values["services"][service]
        assert service_values["enabled"] is True
        assert service_values["image"]["repository"] == f"media-center-{service}"
        assert service_values["serviceName"] == service


def test_issue_248_deployment_template_defines_probes_and_port_7700() -> None:
    values = read_chart_values()
    templates = read_chart_templates()

    assert values["containerPort"] == 7700
    assert values["service"]["port"] == 7700
    assert values["probes"]["liveness"]["path"] == "/health"
    assert values["probes"]["readiness"]["path"] == "/ready"

    required_markers = [
        "kind: Deployment",
        "containerPort: {{ $.Values.containerPort }}",
        "name: http",
        "livenessProbe:",
        "path: {{ $.Values.probes.liveness.path | quote }}",
        "readinessProbe:",
        "path: {{ $.Values.probes.readiness.path | quote }}",
        "port: http",
        "resources:",
        "kind: Service",
        "targetPort: http",
    ]
    missing = [marker for marker in required_markers if marker not in templates]

    assert not missing


def test_issue_248_deployment_template_applies_runtime_hardening() -> None:
    values = read_chart_values()
    templates = read_chart_templates()

    assert values["podSecurityContext"]["runAsUser"] == 1000
    assert values["podSecurityContext"]["runAsGroup"] == 1000
    assert values["podSecurityContext"]["runAsNonRoot"] is True
    assert values["podSecurityContext"]["fsGroup"] == 1000
    assert values["containerSecurityContext"]["readOnlyRootFilesystem"] is True
    assert values["containerSecurityContext"]["allowPrivilegeEscalation"] is False
    assert values["containerSecurityContext"]["capabilities"]["drop"] == ["ALL"]

    required_markers = [
        "securityContext:",
        "toYaml $.Values.podSecurityContext",
        "toYaml $.Values.containerSecurityContext",
        "mountPath: /tmp",
        "mountPath: /app/logs",
        "emptyDir:",
        "medium: Memory",
    ]
    missing = [marker for marker in required_markers if marker not in templates]

    assert not missing


def test_issue_248_service_accounts_use_projected_s2s_token() -> None:
    values = read_chart_values()
    templates = read_chart_templates()

    assert values["serviceAccount"]["create"] is True
    assert values["serviceAccount"]["automount"] is False
    assert values["s2s"]["authMethod"] == "kubernetes_sa"
    assert values["s2s"]["audience"] == "nmc-services"
    assert values["s2s"]["tokenMountPath"] == "/var/run/secrets/nmc/s2s"
    assert values["s2s"]["tokenPath"] == "token"
    assert values["s2s"]["caPath"] == "ca.crt"

    required_markers = [
        "kind: ServiceAccount",
        "automountServiceAccountToken: {{ $.Values.serviceAccount.automount }}",
        "serviceAccountName:",
        "projected:",
        "serviceAccountToken:",
        "audience: {{ $.Values.s2s.audience | quote }}",
        "expirationSeconds: {{ $.Values.s2s.expirationSeconds }}",
        "path: {{ $.Values.s2s.tokenPath | quote }}",
        "name: kube-root-ca.crt",
        "S2S_AUTH_METHOD",
        "S2S_K8S_TOKEN_PATH",
        "S2S_AUDIENCE",
        "S2S_K8S_ISSUER",
        "S2S_K8S_TOKENREVIEW_URL",
        "S2S_K8S_CA_PATH",
        "authentication.k8s.io",
        'resources: ["tokenreviews"]',
        'verbs: ["create"]',
    ]
    missing = [marker for marker in required_markers if marker not in templates]

    assert not missing


def test_issue_248_docs_and_validation_script_cover_helm_contract() -> None:
    docs = "\n".join(
        [
            read_text("docs/STAGE_9_ACCEPTANCE.md"),
            read_text("infra/README.md"),
        ]
    )
    workflow = read_text(".github/workflows/ci.yml")
    validation_script = read_text("experiments/validate_issue248_helm.sh")

    for marker in (
        "#248",
        "E2",
        "deploy/helm/media-center",
        "tests/test_helm_k8s_issue248_contract.py",
        "helm lint",
        "kubeconform",
        "/health",
        "/ready",
        "projected ServiceAccount token",
    ):
        assert marker in docs

    for marker in (
        "helm lint deploy/helm/media-center",
        "helm template media-center deploy/helm/media-center",
        "kubeconform",
    ):
        assert marker in validation_script

    for marker in (
        "Helm/k8s validation",
        "HELM_VERSION: v3.21.2",
        "KUBECONFORM_VERSION: v0.8.0",
        "bash experiments/validate_issue248_helm.sh",
    ):
        assert marker in workflow
