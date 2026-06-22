from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPACITY_PATH = ROOT / "docs/MINIMAL_PRODUCTION_RESOURCES.md"


def test_issue211_capacity_document_is_published() -> None:
    capacity = CAPACITY_PATH.read_text(encoding="utf-8")

    _assert_markers(
        capacity,
        [
            "# Минимальные production-ресурсы НМЦ",
            "Статус: capacity-ready для issue #211",
            "до 100 пользователей в минуту",
            "nmc-minimal-100upm",
            "recommended-core",
            "not_full_ha",
            "no_pdn_no_secrets",
            "tests/test_minimal_production_resources_issue211_contract.py",
            "Комфортный минимум одного FastAPI-сервиса",
        ],
    )


def test_issue211_recommended_services_have_memory_budgets() -> None:
    capacity = CAPACITY_PATH.read_text(encoding="utf-8")

    required_rows = [
        "| API Gateway | 2 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |",
        "| Web Cabinet | 1 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |",
        ("| Activity Command Center | 1 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |"),
        ("| Contribution Ledger | 1 | 0.5 vCPU | 1 vCPU | 512 MiB | 1 GiB |"),
        "| CGLR | 1 | 0.5 vCPU | 1 vCPU | 768 MiB | 1.5 GiB |",
        ("| Messenger Adapter | 1 | 0.5 vCPU | 1 vCPU | 512 MiB | 1 GiB |"),
        ("| HITL Payout Gateway | 1 | 0.5 vCPU | 1 vCPU | 512 MiB | 1 GiB |"),
        "| Wallet | 1 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |",
        ("| Notification Gateway | 1 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |"),
        "| Policy Manager | 1 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |",
        ("| Blockchain Auditor | 1 | 0.5 vCPU | 1 vCPU | 512 MiB | 1 GiB |"),
        "| Analytics Engine | 1 | 0.5 vCPU | 1 vCPU | 512 MiB | 1 GiB |",
    ]

    _assert_markers(capacity, required_rows)
    _assert_markers(
        capacity,
        [
            "App subtotal: 4.75 vCPU requests, 9.5 vCPU limits",
            "5 GiB RAM requests, 10 GiB RAM limits",
        ],
    )


def test_issue211_infrastructure_and_host_floor_are_defined() -> None:
    capacity = CAPACITY_PATH.read_text(encoding="utf-8")

    _assert_markers(
        capacity,
        [
            "PostgreSQL 17",
            "Redis 7.4",
            "RabbitMQ 4.1",
            "ChromaDB 1.5.9",
            "MinIO / S3",
            "Prometheus + Alertmanager + Grafana + OpenTelemetry Collector",
            "Besu QBFT audit-chain",
            "Recommended floor: 16 vCPU, 32 GiB RAM, 300 GiB NVMe",
            "External backup/object storage: 100 GiB",
        ],
    )


def test_issue211_capacity_doc_is_linked_from_primary_docs() -> None:
    for relative_path in (
        "README.md",
        "docs/OPERATIONS_MANUAL.md",
        "docs/LOAD_TESTING.md",
        "infra/README.md",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "docs/MINIMAL_PRODUCTION_RESOURCES.md" in content


def _assert_markers(content: str, markers: list[str]) -> None:
    missing = [marker for marker in markers if marker not in content]

    assert not missing
