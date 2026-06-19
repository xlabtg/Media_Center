from __future__ import annotations

from typing import cast

from analytics_engine import AnalyticsEngineAPIState, create_analytics_engine_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "analytics-engine-issue-61-secret"
PERIOD = "2026-W25"


def test_issue_61_analytics_engine_calculates_kpi_and_tenant_aggregates() -> None:
    app = _app()
    client = TestClient(app)

    for member_index in range(18):
        response = client.post(
            "/analytics/events",
            headers=_headers(
                subject=f"member-{member_index}",
                roles=("member_full",),
                correlation_id=f"corr-active-member-{member_index}",
            ),
            json={
                "event_id": f"evt-active-member-{member_index}",
                "type": "member_active",
                "period": PERIOD,
                "member_id": f"member-{member_index}",
                "occurred_at": "2026-06-19T10:00:00Z",
            },
        )
        assert response.status_code == 201

    for payload in (
        {"event_id": "evt-new-members", "type": "member_joined", "value": 4},
        {"event_id": "evt-materials", "type": "material_published", "value": 24},
        {"event_id": "evt-views", "type": "content_viewed", "value": 12_500},
        {
            "event_id": "evt-reading",
            "type": "reading_time_recorded",
            "value": 24_000,
            "sample_count": 100,
        },
        {"event_id": "evt-comments", "type": "comment_created", "value": 60},
        {"event_id": "evt-tasks", "type": "task_completed", "value": 14},
        {"event_id": "evt-initiatives", "type": "initiative_created", "value": 2},
    ):
        response = client.post(
            "/analytics/events",
            headers=_headers(subject="board-1", roles=("board",)),
            json={
                **payload,
                "period": PERIOD,
                "occurred_at": "2026-06-19T11:00:00Z",
            },
        )
        assert response.status_code == 201

    tenant_b_noise = client.post(
        "/analytics/events",
        headers=_headers(
            tenant_id="tenant-b",
            subject="board-b",
            roles=("board",),
            correlation_id="corr-tenant-b-noise",
        ),
        json={
            "event_id": "evt-tenant-b-views",
            "type": "content_viewed",
            "period": PERIOD,
            "value": 999_999,
            "occurred_at": "2026-06-19T11:05:00Z",
        },
    )
    kpi_response = client.get(
        "/analytics/kpi",
        headers=_headers(subject="council-1", roles=("council",)),
        params={"period": PERIOD},
    )
    aggregates_response = client.get(
        "/analytics/aggregates",
        headers=_headers(subject="council-1", roles=("council",)),
        params={"period": PERIOD},
    )

    assert tenant_b_noise.status_code == 201
    assert kpi_response.status_code == 200
    kpi_body = kpi_response.json()
    assert kpi_body["tenant_id"] == "tenant-a"
    assert kpi_body["period"] == PERIOD
    assert kpi_body["summary"] == {
        "metrics_total": 8,
        "metrics_on_track": 8,
        "metrics_below_target": 0,
        "metrics_above_target": 0,
    }
    metrics = {item["key"]: item for item in kpi_body["metrics"]}
    assert metrics["active_members"]["value"] == 18
    assert metrics["active_members"]["target_min"] == 15
    assert metrics["active_members"]["target_max"] == 25
    assert metrics["active_members"]["status"] == "on_track"
    assert metrics["content_views"]["value"] == 12_500
    assert metrics["content_views"]["target_min"] == 10_000
    assert metrics["avg_reading_minutes"]["value"] == 4
    assert metrics["comments"]["value"] == 60
    assert metrics["tasks_completed"]["value"] == 14
    assert metrics["initiatives_created"]["value"] == 2

    assert aggregates_response.status_code == 200
    aggregate_body = aggregates_response.json()
    categories = {item["category"]: item for item in aggregate_body["categories"]}
    assert categories["participation"]["unique_members"] == 18
    assert categories["participation"]["totals"] == {
        "member_active": 18,
        "member_joined": 4,
    }
    assert categories["content"]["totals"]["content_viewed"] == 12_500
    assert categories["content"]["totals"]["reading_time_recorded"] == 24_000
    assert categories["engagement"]["totals"] == {"comment_created": 60}
    assert categories["actions"]["totals"] == {
        "initiative_created": 2,
        "task_completed": 14,
    }

    state = cast(AnalyticsEngineAPIState, app.state.analytics_engine_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "analytics.event_recorded"
    ] * 26
    assert "member_id" not in state.publisher.messages[0].envelope.payload
    assert "member_hash" in state.publisher.messages[0].envelope.payload


def test_issue_61_analytics_engine_enforces_rbac_and_tenant_context() -> None:
    app = _app()
    client = TestClient(app)

    forbidden = client.get(
        "/analytics/kpi",
        headers=_headers(subject="audience-1", roles=("audience",)),
        params={"period": PERIOD},
    )
    headers = _headers(subject="council-1", roles=("council",))
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.get(
        "/analytics/aggregates",
        headers=headers,
        params={"period": PERIOD},
    )

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"
    assert tenant_override.status_code == 403
    assert tenant_override.json()["error"]["code"] == "tenant_isolation_violation"
    state = cast(AnalyticsEngineAPIState, app.state.analytics_engine_api)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"


def test_issue_61_analytics_engine_docs_are_marked_implemented() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = (root / "docs/modules/analytics-engine.md").read_text(encoding="utf-8")
    readme = (root / "services/analytics-engine/README.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано для #61",
        "POST** `/analytics/events`",
        "GET** `/analytics/kpi?period=`",
        "GET** `/analytics/aggregates?period=`",
        "tenant-isolation контракт #61",
    ):
        assert marker in spec

    for marker in (
        "create_analytics_engine_app",
        "POST /analytics/events",
        "GET /analytics/kpi",
        "InMemoryAnalyticsRepository",
    ):
        assert marker in readme


def _app() -> FastAPI:
    return create_analytics_engine_app(
        ServiceTemplateConfig(
            service_name="analytics-engine",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str = "corr-analytics-issue-61",
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
