from __future__ import annotations

from pathlib import Path
from typing import cast

from activity_command_center import (
    ActivityCommandCenterAPIState,
    create_activity_command_center_app,
)
from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "activity-command-center-issue-54-secret"


def test_issue_54_activity_command_center_acceptance_contract() -> None:
    app = create_activity_command_center_app(
        ServiceTemplateConfig(
            service_name="activity-command-center",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )
    client = TestClient(app)

    updated_thresholds = client.put(
        "/thresholds",
        headers=_headers(subject="council-1", roles=("council",)),
        json={
            "max_autonomous_risk_score": 0.45,
            "min_agent_confidence": 0.75,
            "operational_queue_limit": 10,
            "strategic_queue_limit": 5,
            "adaptive_queue_limit": 2,
            "updated_at": "2026-06-18T12:00:00Z",
            "metadata": {"issue": "54"},
        },
    )
    operational_task = client.post(
        "/tasks",
        headers=_headers(
            subject="agent-7",
            roles=("member_full",),
            correlation_id="corr-issue-54-operational",
        ),
        json={
            "task_id": "task-operational-escalation",
            "event_id": "evt-operational-escalation",
            "task_type": "agent_publication_check",
            "title": "Операционная проверка публикации",
            "payload": {"publication_id": "pub-issue-54"},
            "assignee": "council-queue",
            "agent_id": "agent-7",
            "risk_score": 0.9,
            "agent_confidence": 0.6,
            "feedback_loop": "operational",
            "created_at": "2026-06-18T12:00:00Z",
        },
    )
    strategic_task = client.post(
        "/tasks",
        headers=_headers(
            subject="board-1",
            roles=("board",),
            correlation_id="corr-issue-54-strategic",
        ),
        json={
            "task_id": "task-strategic-threshold-review",
            "task_type": "threshold_review",
            "title": "Стратегический пересмотр порогов",
            "payload": {"policy_area": "publication_moderation"},
            "assignee": "council-1",
            "risk_score": 0.3,
            "agent_confidence": 0.9,
            "feedback_loop": "strategic",
            "created_at": "2026-06-18T12:00:00Z",
        },
    )
    adaptive_task = client.post(
        "/tasks",
        headers=_headers(
            subject="board-1",
            roles=("board",),
            correlation_id="corr-issue-54-adaptive",
        ),
        json={
            "task_id": "task-adaptive-rl-kpi",
            "task_type": "rl_kpi_review",
            "title": "Адаптивный разбор KPI контура",
            "payload": {"metric": "agent_precision"},
            "assignee": "analytics-agent",
            "risk_score": 0.2,
            "agent_confidence": 0.95,
            "feedback_loop": "adaptive",
            "created_at": "2026-06-18T12:00:00Z",
        },
    )
    overview = client.get(
        "/activity/overview",
        headers=_headers(subject="council-1", roles=("council",)),
    )

    assert updated_thresholds.status_code == 200
    assert updated_thresholds.json()["revision"] == 2
    assert operational_task.status_code == 201
    assert operational_task.json()["policy_decision"] == "escalate"
    assert operational_task.json()["due_at"] == "2026-06-18T20:00:00Z"
    assert strategic_task.status_code == 201
    assert strategic_task.json()["policy_decision"] == "allow"
    assert strategic_task.json()["due_at"] == "2026-06-20T12:00:00Z"
    assert adaptive_task.status_code == 201
    assert adaptive_task.json()["due_at"] == "2026-07-02T12:00:00Z"
    assert overview.status_code == 200

    overview_body = overview.json()
    assert overview_body["thresholds"]["revision"] == 2
    assert overview_body["queue_total"] == 3
    assert overview_body["queue_by_status"] == {
        "queued": 2,
        "needs_council_review": 1,
    }
    assert overview_body["feedback_loops"]["operational"]["window_hours"] == 8
    assert overview_body["feedback_loops"]["strategic"]["window_hours"] == 48
    assert overview_body["feedback_loops"]["adaptive"]["window_hours"] == 336

    state = cast(ActivityCommandCenterAPIState, app.state.activity_command_center_api)
    assert [message.envelope.type for message in state.publisher.messages] == [
        "activity.thresholds.updated",
        "activity.task.created",
        "activity.task.created",
        "activity.task.created",
    ]


def test_issue_54_activity_command_center_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/activity-command-center.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "services/activity-command-center/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "**Статус:** 🟢 реализовано",
        "create_activity_command_center_app",
        "ActivityCommandCenter",
        "/activity/overview",
        "/tasks",
        "/thresholds",
        "#54",
    ):
        assert marker in spec

    for marker in (
        "реализован минимальный backend-контур Activity Command Center",
        "Пороги Совета применяются при постановке задач",
        "операционный, стратегический и адаптивный контуры",
    ):
        assert marker in readme


def _headers(
    *,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str = "tenant-a",
    correlation_id: str = "corr-issue-54",
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
