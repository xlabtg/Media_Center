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
JWT_SECRET = "neuro-agent-issue-58-secret"


def test_issue_58_neuro_agent_orchestrator_epic_acceptance_contract() -> None:
    app = create_neuro_agent_orchestrator_app(
        ServiceTemplateConfig(
            service_name="neuro-agent-orchestrator",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )
    client = TestClient(app)
    council_headers = _headers(subject="council-1", roles=("council",))
    agent_headers = _headers(subject="agent-1", roles=("member_full",))

    thresholds = client.put(
        "/thresholds",
        headers=council_headers,
        json={
            "max_autonomous_risk_score": 0.4,
            "min_agent_confidence": 0.8,
            "max_autonomous_recipients": 3,
            "min_content_quality_score": 0.72,
            "allowed_template_keys": ["welcome", "faq_basic"],
            "metadata": {"issue": "58"},
        },
    )
    audience_run = client.post(
        "/agents/run",
        headers=agent_headers,
        json={
            "run_id": "run-issue-58-audience",
            "event_id": "evt-issue-58-audience",
            "task_type": "audience_analysis",
            "audience_sources": [
                {
                    "source_id": "tg-open-audience",
                    "platform": "telegram",
                    "access_scope": "public",
                    "tos_status": "allowed",
                    "legal_basis": "public_interest",
                    "collected_at": "2026-06-19T04:30:00Z",
                    "metrics": {
                        "followers": 1_000,
                        "comments": 80,
                        "reactions": 120,
                        "shares": 20,
                    },
                    "topic_tags": ["governance", "education"],
                }
            ],
        },
    )
    auto_reply_run = client.post(
        "/agents/run",
        headers=agent_headers,
        json={
            "run_id": "run-issue-58-reply",
            "event_id": "evt-issue-58-reply",
            "task_type": "engagement_auto_reply",
            "auto_reply": {
                "trigger_id": "comment-issue-58",
                "platform": "telegram",
                "recipient_ref": "@raw-issue-58-handle",
                "template_key": "welcome",
                "risk_score": 0.2,
                "agent_confidence": 0.91,
                "estimated_recipients": 1,
                "created_at": "2026-06-19T04:35:00Z",
                "context": {"topic": "membership"},
            },
        },
    )
    content_run = client.post(
        "/agents/run",
        headers=agent_headers,
        json={
            "run_id": "run-issue-58-content",
            "event_id": "evt-issue-58-content",
            "task_type": "content_hygiene",
            "content_hygiene": {
                "content_id": "pub-issue-58-safe",
                "platform": "vk",
                "content_text": (
                    "Команда медиацентра публикует спокойный обзор решений Совета, "
                    "поясняет правила участия, показывает ближайшие образовательные "
                    "активности и приглашает участников задавать вопросы через "
                    "официальные каналы обратной связи."
                ),
                "author_ref": "@raw-issue-58-author",
                "created_at": "2026-06-19T04:40:00Z",
                "context": {"campaign": "issue-58"},
            },
        },
    )
    analytics_run = client.post(
        "/agents/run",
        headers=agent_headers,
        json={
            "run_id": "run-issue-58-analytics",
            "event_id": "evt-issue-58-analytics",
            "task_type": "publication_optimization",
            "publication_optimization": {
                "publication_id": "pub-issue-58-low-ctr",
                "platform": "telegram",
                "published_at": "2026-06-19T04:00:00Z",
                "metrics": {
                    "impressions": 10_000,
                    "reach": 9_000,
                    "clicks": 30,
                    "reactions": 70,
                    "comments": 15,
                    "shares": 4,
                    "conversions": 1,
                },
                "topic_tags": ["education"],
                "agent_confidence": 0.65,
                "recommendation_risk_score": 0.55,
                "created_at": "2026-06-19T04:45:00Z",
            },
        },
    )
    proxy_pool = client.put(
        "/proxy-pools/issue-58-publishing",
        headers=council_headers,
        json={
            "event_id": "evt-issue-58-proxy-pool",
            "platform": "telegram",
            "proxies": [
                {
                    "proxy_id": "http-primary",
                    "protocol": "http",
                    "url": "http://issue-58-http-proxy.internal:8080",
                    "priority": 10,
                },
                {
                    "proxy_id": "socks-backup",
                    "protocol": "socks5",
                    "url": "socks5://issue-58-socks-proxy.internal:1080",
                    "priority": 20,
                },
                {
                    "proxy_id": "mtproto-reserve",
                    "protocol": "mtproto",
                    "url": "mtproto://issue-58-mtproto-proxy.internal:443",
                    "secret_ref": "vault://tenant-a/issue-58/mtproto",
                    "priority": 30,
                },
            ],
        },
    )
    first_lease = client.post(
        "/proxy-pools/issue-58-publishing/lease",
        headers=agent_headers,
        json={"event_id": "evt-issue-58-proxy-lease-1"},
    )
    failed_health = client.post(
        "/proxy-pools/issue-58-publishing/health-checks",
        headers=council_headers,
        json={
            "event_id": "evt-issue-58-proxy-health",
            "checks": [
                {
                    "proxy_id": "http-primary",
                    "alive": False,
                    "checked_at": "2026-06-19T04:50:00Z",
                    "reason_code": "tcp_timeout",
                },
                {
                    "proxy_id": "socks-backup",
                    "alive": False,
                    "checked_at": "2026-06-19T04:50:00Z",
                    "reason_code": "connection_refused",
                },
            ],
        },
    )
    failover_lease = client.post(
        "/proxy-pools/issue-58-publishing/lease",
        headers=agent_headers,
        json={"event_id": "evt-issue-58-proxy-lease-failover"},
    )

    assert thresholds.status_code == 200
    assert thresholds.json()["revision"] == 2

    assert audience_run.status_code == 201
    assert audience_run.json()["status"] == "completed"
    assert audience_run.json()["audience_profile"]["public_sources_only"] is True
    assert audience_run.json()["audience_profile"]["total_reach"] == 1_000

    assert auto_reply_run.status_code == 201
    assert auto_reply_run.json()["status"] == "completed"
    assert auto_reply_run.json()["auto_reply"]["status"] == "sent"
    assert "@raw-issue-58-handle" not in auto_reply_run.text

    assert content_run.status_code == 201
    assert content_run.json()["status"] == "completed"
    assert content_run.json()["content_hygiene"]["status"] == "passed"
    assert content_run.json()["content_hygiene"]["content_hash"].startswith("sha256:")
    assert "@raw-issue-58-author" not in content_run.text

    assert analytics_run.status_code == 201
    assert analytics_run.json()["status"] == "needs_council_review"
    assert analytics_run.json()["publication_analytics"]["performance_band"] == "low"
    assert all(
        item["requires_human_approval"] is True
        for item in analytics_run.json()["publication_analytics"]["recommendations"]
    )

    assert proxy_pool.status_code == 200
    assert proxy_pool.json()["healthy_proxy_count"] == 3
    assert first_lease.status_code == 201
    assert first_lease.json()["proxy_id"] == "http-primary"
    assert failed_health.status_code == 200
    assert failed_health.json()["healthy_proxy_count"] == 1
    assert failover_lease.status_code == 201
    assert failover_lease.json()["proxy_id"] == "mtproto-reserve"
    assert "vault://tenant-a/issue-58/mtproto" not in proxy_pool.text

    status = client.get(
        "/agents/status",
        headers=council_headers,
    )
    assert status.status_code == 200
    assert [item["run_id"] for item in status.json()["items"]] == [
        "run-issue-58-audience",
        "run-issue-58-reply",
        "run-issue-58-content",
        "run-issue-58-analytics",
    ]

    state = cast(NeuroAgentOrchestratorAPIState, app.state.neuro_agent_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "neuro_agent.thresholds.updated",
        "neuro_agent.audience_profile.created",
        "neuro_agent.auto_reply.sent",
        "neuro_agent.content_hygiene.passed",
        "neuro_agent.publication_analytics.created",
        "neuro_agent.proxy_pool.updated",
        "neuro_agent.proxy.leased",
        "neuro_agent.proxy_health.checked",
        "neuro_agent.proxy.leased",
    ]
    assert [message.envelope.type for message in state.publisher.messages] == [
        "neuro_agent.thresholds.updated",
        "neuro_agent.audience_profile.created",
        "neuro_agent.auto_reply.sent",
        "neuro_agent.content_hygiene.passed",
        "neuro_agent.publication_analytics.created",
        "neuro_agent.proxy_pool.updated",
        "neuro_agent.proxy.leased",
        "neuro_agent.proxy_health.checked",
        "neuro_agent.proxy.leased",
    ]

    audit_json = "".join(
        record.model_dump_json() for record in state.audit_log_sink.records
    )
    event_json = "".join(
        message.envelope.to_json() for message in state.publisher.messages
    )
    for raw_value in (
        "@raw-issue-58-handle",
        "@raw-issue-58-author",
        "issue-58-http-proxy.internal",
        "issue-58-socks-proxy.internal",
        "issue-58-mtproto-proxy.internal",
        "vault://tenant-a/issue-58/mtproto",
    ):
        assert raw_value not in audit_json
        assert raw_value not in event_json


def test_issue_58_neuro_agent_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/neuro-agent-orchestrator.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "services/neuro-agent-orchestrator/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "**Статус:** 🟢 реализовано",
        "#55",
        "#56",
        "#57",
        "#58",
        (
            "Спецификация синхронизирована с реализацией "
            "Neuro-Agent Orchestrator для issue #58"
        ),
    ):
        assert marker in spec

    for marker in (
        "реализован полный backend-контур Neuro-Agent Orchestrator для issue #58",
        "аудитории, авто-ответов, контент-гигиены, аналитики и ротации прокси",
        "pytest tests/test_neuro_agent_orchestrator_epic_acceptance_contract.py",
    ):
        assert marker in readme


def _headers(
    *,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str = "tenant-a",
    correlation_id: str = "corr-issue-58",
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
