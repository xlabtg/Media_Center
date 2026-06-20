from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "infra/backup/backup-policy.json"
RUNBOOK_PATH = ROOT / "docs/DISASTER_RECOVERY.md"
BACKUP_SCRIPT_PATH = ROOT / "infra/backup/scripts/backup.sh"
RESTORE_DRILL_SCRIPT_PATH = ROOT / "infra/backup/scripts/restore_drill.sh"
CRON_PATH = ROOT / "infra/backup/cron.d/nmc-backups.cron"


def test_issue99_backup_policy_covers_all_persistent_stores() -> None:
    policy = _mapping(json.loads(POLICY_PATH.read_text(encoding="utf-8")))
    targets = [_mapping(target) for target in _sequence(policy["targets"])]
    targets_by_component = {target["component"]: target for target in targets}

    assert policy["status"] == "dr-ready"
    assert policy["issue"] == 99
    assert policy["timezone"] == "UTC"
    assert policy["backup_storage"]["encryption"] == "AES-256"
    assert policy["backup_storage"]["immutability"] == "object-lock-governance"
    assert policy["backup_storage"]["secret_policy"] == "no_pdn_no_secrets"
    assert {"PostgreSQL", "ChromaDB", "S3/MinIO"} <= set(targets_by_component)

    for component in ("PostgreSQL", "ChromaDB", "S3/MinIO"):
        target = targets_by_component[component]
        schedule = _mapping(target["schedule"])
        validation = _sequence(target["restore_validation"])

        assert _is_cron(schedule["full_backup_cron_utc"])
        assert target["rpo_minutes"] <= 60
        assert target["rto_minutes"] <= 240
        assert target["retention"]["daily_days"] >= 14
        assert target["retention"]["monthly_months"] >= 6
        assert "tenant isolation smoke" in validation
        assert "checksum verification" in validation


def test_issue99_restore_drill_records_rto_rpo_and_tested_restore() -> None:
    policy = _mapping(json.loads(POLICY_PATH.read_text(encoding="utf-8")))
    drills = [_mapping(drill) for drill in _sequence(policy["restore_drills"])]

    assert drills
    latest = drills[-1]
    assert latest["result"] == "passed"
    assert latest["evidence_id"] == "drill-issue-99-2026-06-20"
    assert latest["last_tested"] == "2026-06-20"
    assert latest["rto_observed_minutes"] <= latest["rto_target_minutes"]
    assert latest["rpo_observed_minutes"] <= latest["rpo_target_minutes"]
    assert set(latest["components"]) == {"PostgreSQL", "ChromaDB", "S3/MinIO"}
    assert "tenant_restore_integrity" in latest["checks"]
    assert "cross_tenant_access_denied" in latest["checks"]


def test_issue99_backup_and_restore_tooling_is_documented_and_schedulable() -> None:
    backup_script = BACKUP_SCRIPT_PATH.read_text(encoding="utf-8")
    restore_script = RESTORE_DRILL_SCRIPT_PATH.read_text(encoding="utf-8")
    cron = CRON_PATH.read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    for marker in (
        "postgres",
        "chroma",
        "minio",
        "pg_dump",
        "tar -czf",
        "mc mirror",
        "BACKUP_ROOT",
        "--dry-run",
    ):
        assert marker in backup_script

    for marker in (
        "restore_drill",
        "rto_target_minutes",
        "rpo_target_minutes",
        "tenant_restore_integrity",
        "cross_tenant_access_denied",
        "--dry-run",
    ):
        assert marker in restore_script

    for marker in (
        "backup-local",
        "restore-drill",
        "backup-policy",
        "infra/backup/scripts/backup.sh --dry-run all",
        "infra/backup/scripts/restore_drill.sh --dry-run",
    ):
        assert marker in makefile

    assert 'MAILTO=""' in cron
    assert "infra/backup/scripts/backup.sh all" in cron
    assert re.search(r"^\d+ \d+ \* \* \* .+backup.sh all", cron, re.MULTILINE)
    assert re.search(r"^\d+ \d+ 1 \* \* .+restore_drill.sh", cron, re.MULTILINE)


def test_issue99_dr_runbook_is_linked_from_primary_docs() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    docs = "\n".join(
        [
            (ROOT / "README.md").read_text(encoding="utf-8"),
            (ROOT / "infra/README.md").read_text(encoding="utf-8"),
            (ROOT / "infra/local/README.md").read_text(encoding="utf-8"),
            (ROOT / "docs/SECURITY.md").read_text(encoding="utf-8"),
            (ROOT / "docs/SRE_RUNBOOK.md").read_text(encoding="utf-8"),
        ],
    )

    for marker in (
        "# Backup и аварийное восстановление",
        "Статус: dr-ready для issue #99",
        "PostgreSQL",
        "ChromaDB",
        "S3/MinIO",
        "RTO",
        "RPO",
        "restore drill",
        "drill-issue-99-2026-06-20",
        "no_pdn_no_secrets",
        "tenant_id",
        "tests/test_backup_dr_issue99_acceptance_contract.py",
    ):
        assert marker in runbook

    assert "docs/DISASTER_RECOVERY.md" in docs
    assert "infra/backup/backup-policy.json" in docs


def _is_cron(value: object) -> bool:
    assert isinstance(value, str)
    return bool(re.fullmatch(r"\S+ \S+ \S+ \S+ \S+", value))


def _mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: Any) -> list[Any]:
    assert isinstance(value, list)
    return value
