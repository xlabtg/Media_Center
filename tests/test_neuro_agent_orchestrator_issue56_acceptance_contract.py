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
JWT_SECRET = "neuro-agent-issue-56-secret"


def test_issue_56_content_hygiene_analytics_and_optimization_contract() -> None:
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
            "max_autonomous_risk_score": 0.35,
            "min_agent_confidence": 0.85,
            "max_autonomous_recipients": 3,
            "allowed_template_keys": ["welcome", "faq_basic"],
            "min_content_quality_score": 0.72,
            "metadata": {"issue": "56"},
        },
    )
    flagged_content = client.post(
        "/agents/run",
        headers=_headers(
            subject="agent-1",
            roles=("member_full",),
            correlation_id="corr-issue-56-hygiene",
        ),
        json={
            "run_id": "run-issue-56-hygiene",
            "event_id": "evt-issue-56-hygiene",
            "task_type": "content_hygiene",
            "content_hygiene": {
                "content_id": "pub-risky",
                "platform": "telegram",
                "content_text": "СРОЧНО!!! казино 18+ без правил",
                "author_ref": "@raw-risky-author",
                "created_at": "2026-06-18T13:00:00Z",
                "context": {"campaign": "pilot"},
            },
        },
    )
    publication_analytics = client.post(
        "/agents/run",
        headers=_headers(
            subject="agent-1",
            roles=("member_full",),
            correlation_id="corr-issue-56-analytics",
        ),
        json={
            "run_id": "run-issue-56-analytics",
            "event_id": "evt-issue-56-analytics",
            "task_type": "publication_optimization",
            "publication_optimization": {
                "publication_id": "pub-low-engagement",
                "platform": "vk",
                "published_at": "2026-06-18T12:00:00Z",
                "metrics": {
                    "impressions": 10_000,
                    "reach": 8_000,
                    "clicks": 40,
                    "reactions": 80,
                    "comments": 10,
                    "shares": 5,
                    "conversions": 2,
                },
                "topic_tags": ["governance", "education"],
                "agent_confidence": 0.62,
                "recommendation_risk_score": 0.55,
                "created_at": "2026-06-18T14:00:00Z",
            },
        },
    )
    status = client.get(
        "/agents/status",
        headers=_headers(subject="council-1", roles=("council",)),
        params={"task_type": "publication_optimization"},
    )

    assert thresholds.status_code == 200
    assert thresholds.json()["revision"] == 2
    assert thresholds.json()["min_content_quality_score"] == 0.72

    assert flagged_content.status_code == 201
    flagged_body = flagged_content.json()
    assert flagged_body["status"] == "needs_council_review"
    assert flagged_body["policy_decision"] == "escalate"
    assert flagged_body["policy_reasons"] == [
        "content_safety_risk_above_threshold",
        "content_quality_below_threshold",
    ]
    hygiene = flagged_body["content_hygiene"]
    assert hygiene["status"] == "flagged"
    assert hygiene["content_hash"].startswith("sha256:")
    assert hygiene["author_ref_hash"].startswith("sha256:")
    assert hygiene["quality_score"] < 0.72
    assert hygiene["safety_risk_score"] > 0.35
    assert set(hygiene["flags"]) >= {"unsafe_keyword", "too_short"}
    assert "казино" not in flagged_content.text
    assert "@raw-risky-author" not in flagged_content.text

    assert publication_analytics.status_code == 201
    analytics_body = publication_analytics.json()
    assert analytics_body["status"] == "needs_council_review"
    assert analytics_body["policy_decision"] == "escalate"
    report = analytics_body["publication_analytics"]
    assert report["publication_id"] == "pub-low-engagement"
    assert report["engagement_rate"] == 0.0119
    assert report["click_through_rate"] == 0.004
    assert report["conversion_rate"] == 0.05
    assert report["performance_band"] == "low"
    assert [item["action"] for item in report["recommendations"]] == [
        "rewrite_opening_hook",
        "strengthen_call_to_action",
        "test_publication_window",
    ]
    assert all(
        item["status"] == "needs_council_review" for item in report["recommendations"]
    )
    assert all(item["auto_applied"] is False for item in report["recommendations"])
    assert all(
        item["requires_human_approval"] is True for item in report["recommendations"]
    )
    assert report["recommendations"][0]["policy_reasons"] == [
        "risk_score_above_threshold",
        "confidence_below_threshold",
    ]

    assert status.status_code == 200
    assert [item["run_id"] for item in status.json()["items"]] == [
        "run-issue-56-analytics"
    ]
    assert status.json()["items"][0]["publication_analytics"] == report

    state = cast(NeuroAgentOrchestratorAPIState, app.state.neuro_agent_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "neuro_agent.thresholds.updated",
        "neuro_agent.content_hygiene.flagged",
        "neuro_agent.publication_analytics.created",
    ]
    assert [message.envelope.type for message in state.publisher.messages] == [
        "neuro_agent.thresholds.updated",
        "neuro_agent.content_hygiene.flagged",
        "neuro_agent.publication_analytics.created",
    ]
    audit_json = "".join(
        record.model_dump_json() for record in state.audit_log_sink.records
    )
    assert "казино" not in audit_json
    assert "@raw-risky-author" not in audit_json


def test_issue_56_neuro_agent_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/neuro-agent-orchestrator.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "services/neuro-agent-orchestrator/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "#56",
        "ContentHygieneAssessment",
        "PublicationAnalyticsReport",
        "content_hygiene",
        "publication_optimization",
    ):
        assert marker in spec

    for marker in (
        "Контент-гигиена помечает небезопасные или некачественные материалы",
        "Аналитика публикаций рассчитывает engagement, CTR и conversion rate",
        "Рекомендации не применяются автоматически",
    ):
        assert marker in readme


def _headers(
    *,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str = "tenant-a",
    correlation_id: str = "corr-issue-56",
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
