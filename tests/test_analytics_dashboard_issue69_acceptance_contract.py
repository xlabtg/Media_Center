from __future__ import annotations

from pathlib import Path
from typing import Any

from analytics_engine import InMemoryAnalyticsRepository, create_analytics_engine_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from web_cabinet import WebCabinetAPIState, create_web_cabinet_app

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "analytics-dashboard-issue-69-secret"
PERIOD = "2026-W25"
PREVIOUS_PERIOD = "2026-W24"


def test_issue_69_dashboard_visualizes_kpi_slices_and_export() -> None:
    analytics_repository = InMemoryAnalyticsRepository()
    analytics_client = TestClient(_analytics_app(repository=analytics_repository))
    _seed_analytics_events(analytics_client)
    client = TestClient(_web_app(analytics_repository=analytics_repository))

    overview = client.get(
        "/analytics/dashboard/overview",
        headers=_headers(subject="council-1", roles=("council",)),
        params={"period": PERIOD},
    )
    content_slice = client.get(
        "/analytics/dashboard/overview",
        headers=_headers(subject="member-1", roles=("member_full",)),
        params={"period": PERIOD, "category": "content"},
    )
    html = client.get(
        "/analytics/dashboard",
        headers=_headers(subject="board-1", roles=("board",)),
        params={"period": PERIOD},
    )
    export = client.get(
        "/analytics/dashboard/export",
        headers=_headers(subject="council-1", roles=("council",)),
        params={"period": PERIOD, "category": "content"},
    )

    assert overview.status_code == 200
    body = overview.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["period"] == PERIOD
    assert body["category"] is None
    assert body["summary"] == {
        "metrics_total": 8,
        "metrics_on_track": 8,
        "metrics_below_target": 0,
        "metrics_above_target": 0,
    }
    metrics = {item["key"]: item for item in body["metrics"]}
    assert metrics["active_members"]["value"] == 18
    assert metrics["content_views"]["value"] == 12_500
    assert metrics["avg_reading_minutes"]["value"] == 4
    assert metrics["comments"]["value"] == 60
    categories = {item["category"]: item for item in body["categories"]}
    assert categories["participation"]["unique_members"] == 18
    assert categories["content"]["totals"] == {
        "content_viewed": 12_500,
        "material_published": 24,
        "reading_time_recorded": 24_000,
    }
    assert [slice_["period"] for slice_ in body["period_slices"]] == [
        PERIOD,
        PREVIOUS_PERIOD,
    ]
    assert body["period_slices"][0]["event_count"] == 25
    assert body["period_slices"][0]["metrics_on_track"] == 8
    assert body["export_url"] == (f"/analytics/dashboard/export?period={PERIOD}")
    assert "999999" not in overview.text

    assert content_slice.status_code == 200
    content_body = content_slice.json()
    assert content_body["category"] == "content"
    assert [item["category"] for item in content_body["metrics"]] == [
        "content",
        "content",
        "content",
    ]
    assert [item["category"] for item in content_body["categories"]] == ["content"]
    assert content_body["summary"] == {
        "metrics_total": 3,
        "metrics_on_track": 3,
        "metrics_below_target": 0,
        "metrics_above_target": 0,
    }
    assert content_body["export_url"] == (
        f"/analytics/dashboard/export?period={PERIOD}&category=content"
    )

    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert '<meta name="viewport"' in html.text
    assert "@media (max-width: 760px)" in html.text
    assert "Дашборд KPI" in html.text
    assert "Просмотры" in html.text
    assert "12500" in html.text
    assert "Экспорт CSV" in html.text
    assert "999999" not in html.text

    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert export.headers["content-disposition"].startswith("attachment;")
    assert "kind,period,category,key,label,value,status" in export.text
    assert "metric,2026-W25,content,content_views,Просмотры,12500,on_track" in (
        export.text
    )
    assert "aggregate,2026-W25,content,content_viewed,content_viewed,12500," in (
        export.text
    )
    assert "999999" not in export.text


def test_issue_69_dashboard_enforces_rbac_and_tenant_context() -> None:
    analytics_repository = InMemoryAnalyticsRepository()
    analytics_client = TestClient(_analytics_app(repository=analytics_repository))
    _seed_analytics_events(analytics_client)
    app = _web_app(analytics_repository=analytics_repository)
    client = TestClient(app)

    forbidden = client.get(
        "/analytics/dashboard/overview",
        headers=_headers(subject="audience-1", roles=("audience",)),
        params={"period": PERIOD},
    )
    headers = _headers(subject="council-1", roles=("council",))
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.get(
        "/analytics/dashboard/overview",
        headers=headers,
        params={"period": PERIOD},
    )

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"
    assert tenant_override.status_code == 403
    assert tenant_override.json()["error"]["code"] == "tenant_isolation_violation"
    state = app.state.web_cabinet_api
    assert isinstance(state, WebCabinetAPIState)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"


def test_issue_69_dashboard_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/web-cabinet.md").read_text(encoding="utf-8")
    analytics_spec = (ROOT / "docs/modules/analytics-engine.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "services/web-cabinet/README.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано для #67, #68 и #69",
        "GET** `/analytics/dashboard/overview`",
        "GET** `/analytics/dashboard`",
        "GET** `/analytics/dashboard/export`",
        "tenant-isolation контракт #69",
    ):
        assert marker in spec

    for marker in (
        "build_analytics_kpi_response",
        "build_analytics_aggregates_response",
        "дашборда #69",
    ):
        assert marker in analytics_spec

    for marker in (
        "Дашборд KPI",
        "GET /analytics/dashboard/overview",
        "GET /analytics/dashboard/export",
        "InMemoryAnalyticsRepository",
    ):
        assert marker in readme


def _analytics_app(*, repository: InMemoryAnalyticsRepository) -> FastAPI:
    return create_analytics_engine_app(
        ServiceTemplateConfig(
            service_name="analytics-engine",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        repository=repository,
    )


def _web_app(*, analytics_repository: InMemoryAnalyticsRepository) -> FastAPI:
    return create_web_cabinet_app(
        ServiceTemplateConfig(
            service_name="web-cabinet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        analytics_repository=analytics_repository,
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str = "corr-analytics-dashboard-issue-69",
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


def _seed_analytics_events(client: TestClient) -> None:
    for member_index in range(18):
        _record_event(
            client,
            {
                "event_id": f"evt-dashboard-active-{member_index}",
                "type": "member_active",
                "period": PERIOD,
                "member_id": f"member-{member_index}",
                "occurred_at": "2026-06-19T10:00:00Z",
            },
            subject=f"member-{member_index}",
            roles=("member_full",),
        )

    for payload in (
        {"event_id": "evt-dashboard-new-members", "type": "member_joined", "value": 4},
        {
            "event_id": "evt-dashboard-materials",
            "type": "material_published",
            "value": 24,
        },
        {
            "event_id": "evt-dashboard-views",
            "type": "content_viewed",
            "value": 12_500,
        },
        {
            "event_id": "evt-dashboard-reading",
            "type": "reading_time_recorded",
            "value": 24_000,
            "sample_count": 100,
        },
        {"event_id": "evt-dashboard-comments", "type": "comment_created", "value": 60},
        {"event_id": "evt-dashboard-tasks", "type": "task_completed", "value": 14},
        {
            "event_id": "evt-dashboard-initiatives",
            "type": "initiative_created",
            "value": 2,
        },
    ):
        _record_event(
            client,
            {**payload, "period": PERIOD, "occurred_at": "2026-06-19T11:00:00Z"},
            subject="board-1",
            roles=("board",),
        )

    for payload in (
        {
            "event_id": "evt-dashboard-prev-members",
            "type": "member_active",
            "value": 12,
        },
        {
            "event_id": "evt-dashboard-prev-views",
            "type": "content_viewed",
            "value": 8_000,
        },
    ):
        _record_event(
            client,
            {
                **payload,
                "period": PREVIOUS_PERIOD,
                "occurred_at": "2026-06-12T11:00:00Z",
            },
            subject="board-1",
            roles=("board",),
        )

    _record_event(
        client,
        {
            "event_id": "evt-dashboard-tenant-b-views",
            "type": "content_viewed",
            "period": PERIOD,
            "value": 999_999,
            "occurred_at": "2026-06-19T11:05:00Z",
        },
        tenant_id="tenant-b",
        subject="board-b",
        roles=("board",),
    )


def _record_event(
    client: TestClient,
    payload: dict[str, Any],
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...],
) -> None:
    response = client.post(
        "/analytics/events",
        headers=_headers(tenant_id=tenant_id, subject=subject, roles=roles),
        json=payload,
    )
    assert response.status_code == 201
