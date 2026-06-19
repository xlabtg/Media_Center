from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from activity_command_center import (
    ActivityCommandCenterAPIState,
    create_activity_command_center_app,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "activity-command-center-test-secret"


def _app() -> FastAPI:
    return create_activity_command_center_app(
        ServiceTemplateConfig(
            service_name="activity-command-center",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str = "council-1",
    roles: tuple[str, ...] = ("council",),
    correlation_id: str = "corr-activity-1",
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


def test_activity_command_center_openapi_documents_public_endpoints() -> None:
    client = TestClient(_app())

    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["paths"].keys() >= {
        "/activity/overview",
        "/tasks",
        "/thresholds",
    }


def test_thresholds_are_updated_by_council_and_applied_to_agent_tasks() -> None:
    app = _app()
    client = TestClient(app)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)

    thresholds = client.put(
        "/thresholds",
        headers=_headers(subject="council-1"),
        json={
            "max_autonomous_risk_score": 0.4,
            "min_agent_confidence": 0.8,
            "operational_queue_limit": 2,
            "strategic_queue_limit": 3,
            "adaptive_queue_limit": 4,
            "updated_at": now.isoformat().replace("+00:00", "Z"),
            "metadata": {"decision": "council-threshold-review"},
        },
    )
    task = client.post(
        "/tasks",
        headers=_headers(
            subject="agent-1",
            roles=("member_full",),
            correlation_id="corr-activity-task-1",
        ),
        json={
            "task_id": "task-risk-review",
            "event_id": "evt-task-risk-review",
            "task_type": "publication_moderation",
            "title": "Проверить публикацию с высоким риском",
            "payload": {"publication_id": "pub-1"},
            "assignee": "council-queue",
            "agent_id": "agent-1",
            "risk_score": 0.7,
            "agent_confidence": 0.65,
            "feedback_loop": "operational",
            "created_at": now.isoformat().replace("+00:00", "Z"),
        },
    )

    assert thresholds.status_code == 200
    thresholds_body = thresholds.json()
    assert thresholds_body["tenant_id"] == "tenant-a"
    assert thresholds_body["revision"] == 2
    assert thresholds_body["updated_by"] == "council-1"
    assert len(thresholds_body["audit_hash"]) == 64

    assert task.status_code == 201
    task_body = task.json()
    assert task_body["status"] == "needs_council_review"
    assert task_body["policy_decision"] == "escalate"
    assert task_body["policy_revision"] == 2
    assert task_body["due_at"] == "2026-06-18T20:00:00Z"
    assert set(task_body["policy_reasons"]) == {
        "risk_score_above_threshold",
        "confidence_below_threshold",
    }

    state = cast(ActivityCommandCenterAPIState, app.state.activity_command_center_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "activity.thresholds.updated",
        "activity.task.created",
    ]
    assert [message.envelope.type for message in state.publisher.messages] == [
        "activity.thresholds.updated",
        "activity.task.created",
    ]


def test_activity_overview_aggregates_queue_and_feedback_loops() -> None:
    client = TestClient(_app())

    for task_id, feedback_loop, created_at in (
        ("task-operational", "operational", "2026-06-18T12:00:00Z"),
        ("task-strategic", "strategic", "2026-06-18T12:30:00Z"),
        ("task-adaptive", "adaptive", "2026-06-18T13:00:00Z"),
    ):
        response = client.post(
            "/tasks",
            headers=_headers(
                subject="board-1",
                roles=("board",),
                correlation_id=f"corr-{task_id}",
            ),
            json={
                "task_id": task_id,
                "task_type": "feedback_review",
                "title": f"Задача {task_id}",
                "payload": {"source": "acceptance"},
                "assignee": "participant-1",
                "risk_score": 0.2,
                "agent_confidence": 0.95,
                "feedback_loop": feedback_loop,
                "created_at": created_at,
            },
        )
        assert response.status_code == 201

    overview = client.get("/activity/overview", headers=_headers())

    assert overview.status_code == 200
    body = overview.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["queue_total"] == 3
    assert body["queue_by_status"] == {"queued": 3}
    assert body["feedback_loops"] == {
        "operational": {
            "queued": 1,
            "needs_council_review": 0,
            "completed": 0,
            "canceled": 0,
            "window_hours": 8,
        },
        "strategic": {
            "queued": 1,
            "needs_council_review": 0,
            "completed": 0,
            "canceled": 0,
            "window_hours": 48,
        },
        "adaptive": {
            "queued": 1,
            "needs_council_review": 0,
            "completed": 0,
            "canceled": 0,
            "window_hours": 336,
        },
    }


def test_activity_command_center_enforces_rbac_and_tenant_isolation() -> None:
    app = _app()
    client = TestClient(app)

    forbidden_update = client.put(
        "/thresholds",
        headers=_headers(subject="member-1", roles=("member_full",)),
        json={"max_autonomous_risk_score": 0.3},
    )
    headers = _headers()
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.get("/activity/overview", headers=headers)

    assert forbidden_update.status_code == 403
    assert forbidden_update.json()["error"]["code"] == "forbidden"
    assert tenant_override.status_code == 403
    assert tenant_override.json()["error"]["code"] == "tenant_isolation_violation"
    state = cast(ActivityCommandCenterAPIState, app.state.activity_command_center_api)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"
