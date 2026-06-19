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
JWT_SECRET = "neuro-agent-issue-64-secret"


def test_issue_64_agentic_rag_deep_research_and_cua_contract() -> None:
    app = create_neuro_agent_orchestrator_app(
        ServiceTemplateConfig(
            service_name="neuro-agent-orchestrator",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )
    client = TestClient(app)

    tenant_a_headers = _headers(
        subject="researcher-1",
        roles=("member_full",),
        tenant_id="tenant-a",
        correlation_id="corr-issue-64-tenant-a",
    )
    tenant_b_headers = _headers(
        subject="researcher-2",
        roles=("member_full",),
        tenant_id="tenant-b",
        correlation_id="corr-issue-64-tenant-b",
    )
    council_headers = _headers(
        subject="council-1",
        roles=("council",),
        tenant_id="tenant-a",
        correlation_id="corr-issue-64-council",
    )

    tenant_a_documents = client.post(
        "/rag/documents",
        headers=tenant_a_headers,
        json={
            "event_id": "evt-issue-64-rag-upsert-a",
            "documents": [
                {
                    "document_id": "council-regulation",
                    "content": (
                        "Совет утверждает регламент выплат после обсуждения, "
                        "фиксирует решение протоколом и передает участникам "
                        "проверяемую версию правил."
                    ),
                    "source_type": "minutes",
                    "source_ref": "protocol-tenant-a-7",
                    "topic_tags": ["governance", "payouts"],
                    "created_at": "2026-06-19T05:00:00Z",
                },
                {
                    "document_id": "editorial-brief",
                    "content": (
                        "Черновик материала должен объяснять решение Совета, "
                        "показывать источники и оставаться на проверке человека "
                        "до публикации."
                    ),
                    "source_type": "brief",
                    "source_ref": "brief-tenant-a-2",
                    "topic_tags": ["research", "content"],
                    "created_at": "2026-06-19T05:05:00Z",
                },
            ],
        },
    )
    tenant_b_documents = client.post(
        "/rag/documents",
        headers=tenant_b_headers,
        json={
            "event_id": "evt-issue-64-rag-upsert-b",
            "documents": [
                {
                    "document_id": "tenant-b-private-plan",
                    "content": (
                        "Закрытая стратегия tenant-b не должна попадать в выдачу "
                        "tenant-a даже при похожем запросе про регламент выплат."
                    ),
                    "source_type": "minutes",
                    "source_ref": "protocol-tenant-b-secret",
                    "topic_tags": ["governance", "payouts"],
                    "created_at": "2026-06-19T05:10:00Z",
                }
            ],
        },
    )
    rag_run = client.post(
        "/agents/run",
        headers=tenant_a_headers,
        json={
            "run_id": "run-issue-64-rag",
            "event_id": "evt-issue-64-rag-query",
            "task_type": "agentic_rag",
            "rag_query": {
                "query_id": "query-regulation",
                "query_text": "Как Совет утверждает регламент выплат?",
                "limit": 3,
                "source_type": "minutes",
                "created_at": "2026-06-19T05:15:00Z",
            },
        },
    )
    deep_research_run = client.post(
        "/agents/run",
        headers=tenant_a_headers,
        json={
            "run_id": "run-issue-64-research",
            "event_id": "evt-issue-64-research",
            "task_type": "deep_research",
            "deep_research": {
                "research_id": "research-council-regulation",
                "question": (
                    "Подготовь черновик материала о регламенте выплат и роли Совета."
                ),
                "query_text": "регламент выплат Совет черновик источники",
                "limit": 4,
                "topic_tags": ["governance", "content"],
                "created_at": "2026-06-19T05:20:00Z",
            },
        },
    )
    content_agent_run = client.post(
        "/agents/run",
        headers=tenant_a_headers,
        json={
            "run_id": "run-issue-64-cua",
            "event_id": "evt-issue-64-cua",
            "task_type": "content_agent_action",
            "content_agent_action": {
                "action_id": "cua-draft-post",
                "workspace_ref": "cms://tenant-a/editorial/drafts",
                "goal": "Подготовить публикацию по регламенту выплат",
                "proposed_actions": [
                    {
                        "action_type": "draft_content",
                        "target_ref": "cms://tenant-a/posts/draft-42",
                        "summary": "создать черновик публикации",
                    },
                    {
                        "action_type": "attach_citations",
                        "target_ref": "cms://tenant-a/posts/draft-42",
                        "summary": "прикрепить найденные источники",
                    },
                ],
                "risk_score": 0.2,
                "agent_confidence": 0.91,
                "created_at": "2026-06-19T05:25:00Z",
            },
        },
    )
    status = client.get(
        "/agents/status",
        headers=council_headers,
        params={"task_type": "agentic_rag"},
    )

    assert tenant_a_documents.status_code == 201
    assert tenant_a_documents.json()["upserted_count"] == 2
    assert tenant_a_documents.json()["tenant_id"] == "tenant-a"
    assert tenant_b_documents.status_code == 201
    assert tenant_b_documents.json()["tenant_id"] == "tenant-b"

    assert rag_run.status_code == 201
    rag_body = rag_run.json()
    assert rag_body["status"] == "completed"
    assert rag_body["policy_decision"] == "allow"
    rag_answer = rag_body["rag_answer"]
    assert rag_answer["query_id"] == "query-regulation"
    assert rag_answer["retrieval_count"] == 1
    assert [item["document_id"] for item in rag_answer["context_items"]] == [
        "council-regulation"
    ]
    assert "Совет утверждает регламент выплат" in rag_answer["answer_text"]
    assert "Закрытая стратегия tenant-b" not in rag_run.text

    assert deep_research_run.status_code == 201
    research_body = deep_research_run.json()
    assert research_body["status"] == "completed"
    draft = research_body["deep_research_draft"]
    assert draft["research_id"] == "research-council-regulation"
    assert draft["draft_status"] == "drafted"
    assert draft["requires_human_review"] is True
    assert draft["citation_count"] == 2
    assert draft["citations"][0]["document_id"] == "council-regulation"
    assert "Черновик" in draft["draft_text"]
    assert "Закрытая стратегия tenant-b" not in deep_research_run.text

    assert content_agent_run.status_code == 201
    cua_body = content_agent_run.json()
    assert cua_body["status"] == "needs_council_review"
    assert cua_body["policy_decision"] == "escalate"
    assert cua_body["policy_reasons"] == ["human_approval_required"]
    action_plan = cua_body["content_agent_action"]
    assert action_plan["approval_status"] == "awaiting_human_approval"
    assert action_plan["requires_human_approval"] is True
    assert action_plan["auto_executed"] is False
    assert action_plan["workspace_ref_hash"].startswith("sha256:")
    assert all(
        item["target_ref_hash"].startswith("sha256:") for item in action_plan["actions"]
    )
    assert "cms://tenant-a/editorial/drafts" not in content_agent_run.text
    assert "cms://tenant-a/posts/draft-42" not in content_agent_run.text

    assert status.status_code == 200
    assert [item["run_id"] for item in status.json()["items"]] == ["run-issue-64-rag"]

    state = cast(NeuroAgentOrchestratorAPIState, app.state.neuro_agent_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "neuro_agent.rag.documents.upserted",
        "neuro_agent.rag.documents.upserted",
        "neuro_agent.rag.query.completed",
        "neuro_agent.deep_research.draft.created",
        "neuro_agent.content_agent.action_proposed",
    ]
    assert [message.envelope.type for message in state.publisher.messages] == [
        "neuro_agent.rag.documents.upserted",
        "neuro_agent.rag.documents.upserted",
        "neuro_agent.rag.query.completed",
        "neuro_agent.deep_research.draft.created",
        "neuro_agent.content_agent.action_proposed",
    ]
    audit_json = "".join(
        record.model_dump_json() for record in state.audit_log_sink.records
    )
    event_json = "".join(
        message.envelope.to_json() for message in state.publisher.messages
    )
    for raw_value in (
        "Закрытая стратегия tenant-b",
        "protocol-tenant-b-secret",
        "cms://tenant-a/editorial/drafts",
        "cms://tenant-a/posts/draft-42",
    ):
        assert raw_value not in audit_json
        assert raw_value not in event_json


def test_issue_64_neuro_agent_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/neuro-agent-orchestrator.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "services/neuro-agent-orchestrator/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "#64",
        "AgenticRagAnswer",
        "DeepResearchDraft",
        "ContentAgentActionPlan",
        "/rag/documents",
        "agentic_rag",
        "deep_research",
        "content_agent_action",
    ):
        assert marker in spec

    for marker in (
        "Agentic RAG возвращает tenant-scoped контекст",
        "DeepResearch формирует черновик материала",
        "Content Agent (CUA) не исполняет действия без human approval",
        "pytest tests/test_neuro_agent_orchestrator_issue64_acceptance_contract.py",
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
