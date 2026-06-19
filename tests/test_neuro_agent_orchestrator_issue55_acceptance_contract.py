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
JWT_SECRET = "neuro-agent-issue-55-secret"


def test_issue_55_audience_parsing_and_auto_reply_acceptance_contract() -> None:
    app = create_neuro_agent_orchestrator_app(
        ServiceTemplateConfig(
            service_name="neuro-agent-orchestrator",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )
    client = TestClient(app)

    thresholds = client.put(
        "/thresholds",
        headers=_headers(subject="council-1", roles=("council",)),
        json={
            "max_autonomous_risk_score": 0.4,
            "min_agent_confidence": 0.8,
            "max_autonomous_recipients": 3,
            "allowed_template_keys": ["welcome", "faq_basic"],
            "metadata": {"issue": "55"},
        },
    )
    audience_run = client.post(
        "/agents/run",
        headers=_headers(
            subject="agent-1",
            roles=("member_full",),
            correlation_id="corr-issue-55-audience",
        ),
        json={
            "run_id": "run-issue-55-audience",
            "event_id": "evt-issue-55-audience",
            "task_type": "audience_analysis",
            "audience_sources": [
                {
                    "source_id": "tg-public-weekly",
                    "platform": "telegram",
                    "access_scope": "public",
                    "tos_status": "allowed",
                    "legal_basis": "public_interest",
                    "collected_at": "2026-06-18T12:00:00Z",
                    "metrics": {
                        "followers": 120,
                        "comments": 12,
                        "reactions": 36,
                    },
                    "topic_tags": ["governance", "education", "media"],
                },
                {
                    "source_id": "vk-open-group",
                    "platform": "vk",
                    "access_scope": "public",
                    "tos_status": "allowed",
                    "legal_basis": "consent",
                    "collected_at": "2026-06-18T12:05:00Z",
                    "metrics": {
                        "followers": 80,
                        "comments": 8,
                        "reactions": 16,
                    },
                    "topic_tags": ["education", "mutual_aid"],
                },
            ],
        },
    )
    auto_reply_run = client.post(
        "/agents/run",
        headers=_headers(
            subject="agent-1",
            roles=("member_full",),
            correlation_id="corr-issue-55-reply",
        ),
        json={
            "run_id": "run-issue-55-reply",
            "event_id": "evt-issue-55-reply",
            "task_type": "engagement_auto_reply",
            "auto_reply": {
                "trigger_id": "comment-42",
                "platform": "telegram",
                "recipient_ref": "@raw-public-handle",
                "template_key": "welcome",
                "risk_score": 0.2,
                "agent_confidence": 0.92,
                "estimated_recipients": 1,
                "created_at": "2026-06-18T12:10:00Z",
                "context": {"topic": "membership"},
            },
        },
    )
    escalated_reply = client.post(
        "/agents/run",
        headers=_headers(
            subject="agent-1",
            roles=("member_full",),
            correlation_id="corr-issue-55-escalated",
        ),
        json={
            "run_id": "run-issue-55-escalated",
            "event_id": "evt-issue-55-escalated",
            "task_type": "engagement_auto_reply",
            "auto_reply": {
                "trigger_id": "comment-risky",
                "platform": "vk",
                "recipient_ref": "vk-raw-user-7",
                "template_key": "welcome",
                "risk_score": 0.75,
                "agent_confidence": 0.61,
                "estimated_recipients": 10,
                "created_at": "2026-06-18T12:15:00Z",
                "context": {"topic": "money"},
            },
        },
    )
    rejected_private_source = client.post(
        "/agents/run",
        headers=_headers(
            subject="agent-1",
            roles=("member_full",),
            correlation_id="corr-issue-55-private",
        ),
        json={
            "run_id": "run-issue-55-private",
            "task_type": "audience_analysis",
            "audience_sources": [
                {
                    "source_id": "private-import",
                    "platform": "telegram",
                    "access_scope": "private",
                    "tos_status": "allowed",
                    "legal_basis": "missing",
                    "collected_at": "2026-06-18T12:20:00Z",
                    "metrics": {"followers": 10},
                    "personal_data_fields": ["phone", "handle"],
                },
            ],
        },
    )
    status = client.get(
        "/agents/status",
        headers=_headers(subject="council-1", roles=("council",)),
    )

    assert thresholds.status_code == 200
    assert thresholds.json()["revision"] == 2
    assert audience_run.status_code == 201
    assert audience_run.json()["status"] == "completed"
    assert audience_run.json()["policy_decision"] == "allow"
    audience_result = audience_run.json()["audience_profile"]
    assert audience_result["public_sources_only"] is True
    assert audience_result["personal_data_fields"] == []
    assert audience_result["source_count"] == 2
    assert audience_result["total_reach"] == 200
    assert audience_result["topic_tags"] == [
        "education",
        "governance",
        "media",
        "mutual_aid",
    ]
    assert auto_reply_run.status_code == 201
    assert auto_reply_run.json()["status"] == "completed"
    assert auto_reply_run.json()["auto_reply"]["status"] == "sent"
    assert auto_reply_run.json()["auto_reply"]["recipient_ref_hash"].startswith(
        "sha256:"
    )
    assert "@raw-public-handle" not in auto_reply_run.text
    assert escalated_reply.status_code == 201
    assert escalated_reply.json()["status"] == "needs_council_review"
    assert escalated_reply.json()["policy_decision"] == "escalate"
    assert escalated_reply.json()["policy_reasons"] == [
        "risk_score_above_threshold",
        "confidence_below_threshold",
        "recipient_limit_exceeded",
    ]
    assert escalated_reply.json()["auto_reply"]["status"] == "needs_council_review"
    assert rejected_private_source.status_code == 422
    assert rejected_private_source.json()["error"]["code"] == "pdn_scope_violation"
    assert status.status_code == 200
    assert [item["run_id"] for item in status.json()["items"]] == [
        "run-issue-55-audience",
        "run-issue-55-reply",
        "run-issue-55-escalated",
    ]

    state = cast(NeuroAgentOrchestratorAPIState, app.state.neuro_agent_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "neuro_agent.thresholds.updated",
        "neuro_agent.audience_profile.created",
        "neuro_agent.auto_reply.sent",
        "neuro_agent.auto_reply.escalated",
    ]
    assert [message.envelope.type for message in state.publisher.messages] == [
        "neuro_agent.thresholds.updated",
        "neuro_agent.audience_profile.created",
        "neuro_agent.auto_reply.sent",
        "neuro_agent.auto_reply.escalated",
    ]
    assert "@raw-public-handle" not in "".join(
        record.model_dump_json() for record in state.audit_log_sink.records
    )
    assert "vk-raw-user-7" not in "".join(
        record.model_dump_json() for record in state.audit_log_sink.records
    )


def test_issue_55_neuro_agent_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/neuro-agent-orchestrator.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "services/neuro-agent-orchestrator/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "**Статус:** 🟢 реализовано",
        "create_neuro_agent_orchestrator_app",
        "AudienceSource",
        "AutoReplyDecision",
        "/agents/run",
        "/agents/status",
        "#55",
    ):
        assert marker in spec

    for marker in (
        "реализован минимальный backend-контур Neuro-Agent Orchestrator",
        "Сбор аудитории допускает только открытые источники",
        "Авто-ответы исполняются только в пределах порогов Совета",
    ):
        assert marker in readme


def _headers(
    *,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str = "tenant-a",
    correlation_id: str = "corr-issue-55",
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
