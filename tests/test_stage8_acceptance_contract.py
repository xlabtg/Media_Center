from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def read_json(relative_path: str) -> dict[str, Any]:
    raw = json.loads(read_text(relative_path))
    assert isinstance(raw, dict)
    return raw


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue103_stage8_acceptance_snapshot_covers_epic_criteria() -> None:
    assert_markers(
        "docs/STAGE_8_ACCEPTANCE.md",
        [
            "Статус: acceptance snapshot для issue #103",
            "## 1. Решение по этапу 8",
            "несколько tenant'ов работают стабильно",
            "изоляция сохраняется под нагрузкой",
            "ресурсы управляются по tenant",
            "SLA/SLO определены и мониторятся",
            "алертинг настроен и протестирован",
            "бэкапы выполняются по расписанию",
            "restore drill прошел в пределах RTO/RPO",
            "каталог отображает tenant'ов",
            "подключение проходит модерацию",
            "RL-KPI loop работает под контролем Совета",
            "эксплуатационная документация и обучение опубликованы",
            "## 8. Gate промышленной эксплуатации",
            "## 9. Локальная проверка",
            "tests/test_stage8_acceptance_contract.py",
        ],
    )


def test_issue103_stage8_acceptance_links_all_child_artifacts() -> None:
    acceptance = read_text("docs/STAGE_8_ACCEPTANCE.md")

    for issue in range(97, 103):
        assert f"issue #{issue}" in acceptance

    for marker in (
        "docs/MULTITENANT_SCALING.md",
        "tests/test_multitenant_scaling_issue97_contract.py",
        "docs/SRE_RUNBOOK.md",
        "infra/observability/slo-targets.json",
        "infra/observability/prometheus/rules/sre-alerts.yml",
        "tests/test_sre_issue98_acceptance_contract.py",
        "docs/DISASTER_RECOVERY.md",
        "infra/backup/backup-policy.json",
        "infra/backup/cron.d/nmc-backups.cron",
        "tests/test_backup_dr_issue99_acceptance_contract.py",
        "docs/TENANT_MARKETPLACE.md",
        "libs/shared/tenant_marketplace.py",
        "tests/test_tenant_marketplace_issue100_acceptance_contract.py",
        "docs/modules/analytics-engine.md",
        "docs/modules/policy-manager.md",
        "tests/test_rl_kpi_loop_issue101_acceptance_contract.py",
        "docs/OPERATIONS_MANUAL.md",
        "docs/TENANT_TRAINING_PROGRAM.md",
        "docs/KNOWLEDGE_BASE.md",
        "docs/operations/tenant-training-record.json",
        "tests/test_operations_training_issue102_acceptance_contract.py",
    ):
        assert marker in acceptance


def test_issue103_stage8_operational_packet_matches_epic_completion() -> None:
    slo_catalog = read_json("infra/observability/slo-targets.json")
    backup_policy = read_json("infra/backup/backup-policy.json")
    training_record = read_json("docs/operations/tenant-training-record.json")
    marketplace_doc = read_text("docs/TENANT_MARKETPLACE.md")
    scaling_doc = read_text("docs/MULTITENANT_SCALING.md")
    analytics_doc = read_text("docs/modules/analytics-engine.md")

    assert slo_catalog["status"] == "sre-ready"
    assert backup_policy["status"] == "dr-ready"
    assert training_record["status"] == "training-complete"
    assert training_record["evidence_policy"] == "no_pdn_no_secrets"
    assert "InMemoryTenantResourceManager" in scaling_doc
    assert "деградации изоляции" in scaling_doc
    assert "tenant-local" in scaling_doc
    assert "модерац" in marketplace_doc.lower()
    assert "каталог" in marketplace_doc.lower()
    assert "RL-KPI" in analytics_doc

    services = [_mapping(service) for service in _sequence(slo_catalog["services"])]
    assert len(services) >= 6
    assert all(service["business_sla_percent"] >= 99 for service in services)
    assert all("tenant_id" in service["metric_labels"] for service in services)

    targets = [_mapping(target) for target in _sequence(backup_policy["targets"])]
    assert {"PostgreSQL", "ChromaDB", "S3/MinIO"} <= {
        str(target["component"]) for target in targets
    }
    assert all(_mapping(target)["rpo_minutes"] <= 60 for target in targets)
    assert all(_mapping(target)["rto_minutes"] <= 240 for target in targets)

    drills = [_mapping(drill) for drill in _sequence(backup_policy["restore_drills"])]
    assert drills[-1]["result"] == "passed"
    assert drills[-1]["rto_observed_minutes"] <= drills[-1]["rto_target_minutes"]
    assert drills[-1]["rpo_observed_minutes"] <= drills[-1]["rpo_target_minutes"]


def test_stage8_acceptance_is_discoverable_from_readme_and_roadmap() -> None:
    assert "docs/STAGE_8_ACCEPTANCE.md" in read_text("README.md")
    assert "docs/STAGE_8_ACCEPTANCE.md" in read_text("docs/ROADMAP.md")


def _mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: Any) -> list[Any]:
    assert isinstance(value, list)
    return value
