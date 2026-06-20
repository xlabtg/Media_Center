from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue_12_security_baseline_covers_threat_model_controls_and_tests() -> None:
    assert_markers(
        "docs/SECURITY.md",
        [
            "Статус: baseline для issue #12",
            "## 5. Детальная модель угроз STRIDE",
            "### 5.1. Границы доверия и активы",
            "DF-01. Аутентификация и сессии",
            "DF-02. Tenant isolation",
            "DF-03. HITL-выплаты и вето",
            "DF-04. Audit-chain",
            "STRIDE-трассировка",
            "## 6. Приоритизированный план контрмер",
            "P0",
            "P1",
            "P2",
            "## 7. План тестов безопасности этапа 6",
            "tenant_isolation_violation",
            "2FA",
            "SCA",
            "gitleaks",
            "pentest",
        ],
    )


def test_issue_86_pentest_report_documents_finding_fix_and_retest() -> None:
    retest_command = (
        "pytest tests/test_hitl_payout_queue_veto.py::"
        "test_payout_audit_metadata_redacts_sensitive_payment_fields"
    )
    assert_markers(
        "docs/SECURITY_PENTEST_ISSUE_86.md",
        [
            "OWASP Top 10:2025",
            "F-86-01",
            "Severity: High",
            "audit_safe_metadata()",
            retest_command,
            "Статус: повторная проверка пройдена",
        ],
    )
