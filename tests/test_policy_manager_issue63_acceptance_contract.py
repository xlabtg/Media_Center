from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from policy_manager import PolicyManagerAPIState, create_policy_manager_app

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "policy-manager-issue-63-secret"


def test_issue_63_policy_manager_versions_applies_and_audits_policies() -> None:
    app = _app()
    client = TestClient(app)

    defaults = client.get(
        "/policies",
        headers=_headers(subject="board-1", roles=("board",)),
    )
    updated_policy = client.put(
        "/policies/automation.max_autonomous_risk_score",
        headers=_headers(subject="council-1", roles=("council",)),
        json={
            "value": {
                "kind": "threshold",
                "target": "risk_score",
                "operator": "lte",
                "threshold": 0.42,
                "reason": "risk_score_above_threshold",
                "decision_on_violation": "escalate",
            },
            "updated_at": "2026-06-19T12:00:00Z",
            "metadata": {"issue": "63", "decision": "council-risk-review"},
        },
    )
    escalated = client.post(
        "/policies/apply",
        headers=_headers(
            subject="agent-1",
            roles=("member_full",),
            correlation_id="corr-policy-apply-risk",
        ),
        json={
            "policy_keys": ["automation.max_autonomous_risk_score"],
            "facts": {"risk_score": 0.55},
            "applied_at": "2026-06-19T12:05:00Z",
        },
    )
    allowed = client.post(
        "/policies/apply",
        headers=_headers(
            subject="agent-1",
            roles=("member_full",),
            correlation_id="corr-policy-apply-allow",
        ),
        json={
            "policy_keys": ["automation.max_autonomous_risk_score"],
            "facts": {"risk_score": 0.3},
            "applied_at": "2026-06-19T12:06:00Z",
        },
    )
    history = client.get(
        "/policies/automation.max_autonomous_risk_score/history",
        headers=_headers(subject="council-1", roles=("council",)),
    )

    assert defaults.status_code == 200
    default_policies = {item["key"]: item for item in defaults.json()["items"]}
    assert default_policies["automation.max_autonomous_risk_score"]["version"] == 1
    assert default_policies["hitl.veto_window_hours"]["value"]["default"] == 8

    assert updated_policy.status_code == 200
    updated_body = updated_policy.json()
    assert updated_body["tenant_id"] == "tenant-a"
    assert updated_body["key"] == "automation.max_autonomous_risk_score"
    assert updated_body["version"] == 2
    assert updated_body["updated_by"] == "council-1"
    assert len(updated_body["audit_hash"]) == 64

    assert escalated.status_code == 200
    assert escalated.json() == {
        "tenant_id": "tenant-a",
        "decision": "escalate",
        "policy_versions": {"automation.max_autonomous_risk_score": 2},
        "reasons": ["risk_score_above_threshold"],
        "applied_at": "2026-06-19T12:05:00Z",
    }
    assert allowed.status_code == 200
    assert allowed.json()["decision"] == "allow"
    assert allowed.json()["reasons"] == []

    assert history.status_code == 200
    assert [item["version"] for item in history.json()["items"]] == [1, 2]

    state = cast(PolicyManagerAPIState, app.state.policy_manager_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "policy.updated",
    ]
    assert [message.envelope.type for message in state.publisher.messages] == [
        "policy.updated",
    ]
    assert state.publisher.messages[0].envelope.payload == {
        "key": "automation.max_autonomous_risk_score",
        "version": 2,
        "audit_hash": updated_body["audit_hash"],
    }


def test_issue_63_policy_manager_enforces_rbac_and_tenant_context() -> None:
    app = _app()
    client = TestClient(app)

    forbidden = client.put(
        "/policies/automation.max_autonomous_risk_score",
        headers=_headers(subject="member-1", roles=("member_full",)),
        json={"value": {"kind": "threshold", "threshold": 0.2}},
    )
    headers = _headers(subject="council-1", roles=("council",))
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.get("/policies", headers=headers)

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"
    assert tenant_override.status_code == 403
    assert tenant_override.json()["error"]["code"] == "tenant_isolation_violation"

    state = cast(PolicyManagerAPIState, app.state.policy_manager_api)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"


def test_issue_63_policy_manager_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/policy-manager.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/policy-manager/README.md").read_text(encoding="utf-8")

    for marker in (
        "**Статус:** 🟢 реализовано",
        "create_policy_manager_app",
        "GET** `/policies`",
        "PUT** `/policies/{key}`",
        "POST** `/policies/apply`",
        "policy.updated",
        "#63",
    ):
        assert marker in spec

    for marker in (
        "централизованный in-memory контур Policy Manager",
        "PolicyManager",
        "InMemoryPolicyRepository",
        "POST /policies/apply",
    ):
        assert marker in readme


def _app() -> FastAPI:
    return create_policy_manager_app(
        ServiceTemplateConfig(
            service_name="policy-manager",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )


def _headers(
    *,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str = "tenant-a",
    correlation_id: str = "corr-policy-issue-63",
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": list(roles),
        },
        JWT_SECRET,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": correlation_id,
    }
