from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_295_service_discovery_contract_is_documented() -> None:
    docs = "\n".join(
        [
            read_text("docs/SERVICE_DISCOVERY.md"),
            read_text("docs/ARCHITECTURE.md"),
            read_text("docs/contracts/sync-api.md"),
            read_text("infra/local/README.md"),
        ]
    )

    required_markers = [
        "#295",
        "Вопрос закрыт",
        "без отдельного service registry",
        "Docker Compose DNS",
        "postgres:5432",
        "redis:6379",
        "rabbitmq:5672",
        "http://minio:9000",
        "http://otel-collector:4318",
        "http://<service>:7700",
        "Kubernetes Service",
        "ClusterIP",
        "<release>-media-center-<service>",
        "S2S credentials не являются механизмом discovery",
        "endpoint из env",
    ]
    missing = [marker for marker in required_markers if marker not in docs]

    assert not missing


def test_issue_295_local_compose_uses_runtime_dns_names_for_dependencies() -> None:
    compose = read_text("infra/local/docker-compose.yml")
    local_readme = read_text("infra/local/README.md")

    for marker in (
        "@postgres:5432",
        "redis://redis:6379/0",
        "@rabbitmq:5672/",
        "CHROMA_HOST: chroma",
        'S3_ENDPOINT_URL: "http://minio:9000"',
        'OTEL_EXPORTER_OTLP_ENDPOINT: "http://otel-collector:4318"',
        'expose:\n    - "7700"',
        "depends_on:",
        "condition: service_healthy",
    ):
        assert marker in compose

    for marker in (
        "Docker Compose DNS",
        "http://api-gateway:7700",
        "Host-порты `7701`-`7714`",
    ):
        assert marker in local_readme


def test_issue_295_helm_services_are_stable_clusterip_dns_targets() -> None:
    values = read_text("deploy/helm/media-center/values.yaml")
    service_template = read_text("deploy/helm/media-center/templates/service.yaml")
    helpers = read_text("deploy/helm/media-center/templates/_helpers.tpl")

    for marker in (
        "type: ClusterIP",
        "port: 7700",
    ):
        assert marker in values

    for marker in (
        "kind: Service",
        'name: {{ include "media-center.serviceFullname"',
        "targetPort: http",
        "app.kubernetes.io/component: {{ $serviceName }}",
    ):
        assert marker in service_template

    for marker in (
        'define "media-center.serviceFullname"',
        'printf "%s-%s" (include "media-center.fullname" .root) .serviceName',
    ):
        assert marker in helpers
