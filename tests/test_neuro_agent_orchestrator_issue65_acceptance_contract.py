from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from neuro_agent_orchestrator import (
    NeuroAgentOrchestratorAPIState,
    create_neuro_agent_orchestrator_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "neuro-agent-issue-65-secret"


def test_issue_65_xai_decision_explanations_are_audited_and_council_visible() -> None:
    app = create_neuro_agent_orchestrator_app(
        ServiceTemplateConfig(
            service_name="neuro-agent-orchestrator",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )
    client = TestClient(app)

    agent_headers = _headers(
        subject="agent-1",
        roles=("member_full",),
        tenant_id="tenant-a",
        correlation_id="corr-issue-65-agent",
    )
    council_headers = _headers(
        subject="council-1",
        roles=("council",),
        tenant_id="tenant-a",
        correlation_id="corr-issue-65-council",
    )
    tenant_b_council_headers = _headers(
        subject="council-2",
        roles=("council",),
        tenant_id="tenant-b",
        correlation_id="corr-issue-65-tenant-b",
    )

    auto_reply_run = client.post(
        "/agents/run",
        headers=agent_headers,
        json={
            "run_id": "run-issue-65-reply",
            "event_id": "evt-issue-65-reply",
            "task_type": "engagement_auto_reply",
            "auto_reply": {
                "trigger_id": "comment-issue-65",
                "platform": "telegram",
                "recipient_ref": "@raw-issue-65-handle",
                "template_key": "welcome",
                "risk_score": 0.7,
                "agent_confidence": 0.6,
                "estimated_recipients": 8,
                "created_at": "2026-06-19T06:00:00Z",
                "context": {"topic": "membership"},
            },
        },
    )
    content_agent_run = client.post(
        "/agents/run",
        headers=agent_headers,
        json={
            "run_id": "run-issue-65-cua",
            "event_id": "evt-issue-65-cua",
            "task_type": "content_agent_action",
            "content_agent_action": {
                "action_id": "cua-issue-65",
                "workspace_ref": "cms://tenant-a/editorial/xai",
                "goal": "Подготовить пояснение решения AI для Совета",
                "proposed_actions": [
                    {
                        "action_type": "draft_content",
                        "target_ref": "cms://tenant-a/posts/xai-draft",
                        "summary": "создать черновик пояснения",
                    }
                ],
                "risk_score": 0.2,
                "agent_confidence": 0.9,
                "created_at": "2026-06-19T06:05:00Z",
            },
        },
    )
    council_explanations = client.get(
        "/agents/explanations",
        headers=council_headers,
    )
    member_explanations = client.get(
        "/agents/explanations",
        headers=agent_headers,
    )
    tenant_b_explanations = client.get(
        "/agents/explanations",
        headers=tenant_b_council_headers,
    )

    assert auto_reply_run.status_code == 201
    reply_body = auto_reply_run.json()
    assert reply_body["status"] == "needs_council_review"
    assert reply_body["policy_decision"] == "escalate"
    reply_explanation = reply_body["decision_explanation"]
    assert reply_explanation["run_id"] == "run-issue-65-reply"
    assert reply_explanation["task_type"] == "engagement_auto_reply"
    assert reply_explanation["policy_decision"] == "escalate"
    assert reply_explanation["status"] == "needs_council_review"
    assert reply_explanation["reason_codes"] == [
        "risk_score_above_threshold",
        "confidence_below_threshold",
        "recipient_limit_exceeded",
    ]
    assert reply_explanation["action_refs"] == ["auto_reply:comment-issue-65"]
    assert reply_explanation["input_facts"]["risk_score"] == 0.7
    assert reply_explanation["input_facts"]["agent_confidence"] == 0.6
    assert reply_explanation["summary"]
    assert len(reply_explanation["explanation_hash"]) == 64
    assert reply_explanation["audit_hash"] == reply_body["audit_hash"]

    assert content_agent_run.status_code == 201
    cua_body = content_agent_run.json()
    cua_explanation = cua_body["decision_explanation"]
    assert cua_body["policy_decision"] == "escalate"
    assert cua_explanation["reason_codes"] == ["human_approval_required"]
    assert cua_explanation["action_refs"] == ["content_agent_action:cua-issue-65"]
    assert cua_explanation["input_facts"]["auto_executed"] is False
    assert cua_explanation["input_facts"]["requires_human_approval"] is True
    assert cua_explanation["audit_hash"] == cua_body["audit_hash"]

    assert council_explanations.status_code == 200
    explanation_items = council_explanations.json()["items"]
    assert [item["run_id"] for item in explanation_items] == [
        "run-issue-65-reply",
        "run-issue-65-cua",
    ]
    assert explanation_items[0] == reply_explanation
    assert explanation_items[1] == cua_explanation

    assert member_explanations.status_code == 403
    assert member_explanations.json()["error"]["code"] == "forbidden"

    assert tenant_b_explanations.status_code == 200
    assert tenant_b_explanations.json()["items"] == []

    state = cast(NeuroAgentOrchestratorAPIState, app.state.neuro_agent_api)
    audit_hashes = [
        record.metadata["decision_explanation_hash"]
        for record in state.audit_log_sink.records
    ]
    assert audit_hashes == [
        reply_explanation["explanation_hash"],
        cua_explanation["explanation_hash"],
    ]
    assert [
        message.envelope.payload["decision_explanation_hash"]
        for message in state.publisher.messages
    ] == audit_hashes

    audit_json = "".join(
        record.model_dump_json() for record in state.audit_log_sink.records
    )
    event_json = "".join(
        message.envelope.to_json() for message in state.publisher.messages
    )
    for raw_value in (
        "@raw-issue-65-handle",
        "cms://tenant-a/editorial/xai",
        "cms://tenant-a/posts/xai-draft",
    ):
        assert raw_value not in audit_json
        assert raw_value not in event_json


def test_issue_65_neuro_agent_xai_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/neuro-agent-orchestrator.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "services/neuro-agent-orchestrator/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "#65",
        "DecisionExplanation",
        "GET** `/agents/explanations`",
        "neuro_agent.decision_explanation",
    ):
        assert marker in spec

    for marker in (
        "XAI-аудит решений AI",
        "DecisionExplanation",
        "GET /agents/explanations",
        "pytest tests/test_neuro_agent_orchestrator_issue65_acceptance_contract.py",
    ):
        assert marker in readme


def _headers(
    *,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str,
    correlation_id: str,
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
