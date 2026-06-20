from __future__ import annotations

from typing import cast

from analytics_engine import AnalyticsEngineAPIState, create_analytics_engine_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "pilot-telemetry-issue-92-secret"
PERIOD = "2026-W26"


def test_issue_92_pilot_collector_records_kpi_telemetry_and_council_report() -> None:
    app = _app()
    client = TestClient(app)

    collect_response = client.post(
        "/analytics/pilot/telemetry/collect",
        headers=_headers(subject="board-1", roles=("board",)),
        json={
            "batch_id": "pilot-batch-week-26",
            "source": "pilot-observability",
            "period": PERIOD,
            "collected_at": "2026-06-26T18:00:00Z",
            "kpi": {
                "active_members": 20,
                "new_members": 4,
                "materials_published": 25,
                "content_views": 12_500,
                "reading_seconds": 27_000,
                "reading_sessions": 100,
                "comments": 64,
                "tasks_completed": 12,
                "initiatives_created": 1,
            },
            "usage": [
                {
                    "signal_id": "usage-dashboard-views",
                    "name": "dashboard_views",
                    "value": 44,
                    "unit": "events",
                    "occurred_at": "2026-06-26T17:50:00Z",
                },
                {
                    "signal_id": "usage-voice-submits",
                    "name": "voice_submissions",
                    "value": 6,
                    "unit": "events",
                    "occurred_at": "2026-06-26T17:55:00Z",
                },
            ],
            "incidents": [
                {
                    "incident_id": "incident-notification-delay",
                    "severity": "medium",
                    "status": "resolved",
                    "title": "Задержка уведомлений",
                    "impact": "Часть участников получила уведомления позже SLA",
                    "occurred_at": "2026-06-26T12:00:00Z",
                    "resolved_at": "2026-06-26T12:20:00Z",
                }
            ],
        },
    )
    tenant_b_noise = client.post(
        "/analytics/pilot/telemetry/collect",
        headers=_headers(
            tenant_id="tenant-b",
            subject="board-b",
            roles=("board",),
            correlation_id="corr-pilot-telemetry-tenant-b",
        ),
        json={
            "batch_id": "pilot-batch-week-26-tenant-b",
            "source": "pilot-observability",
            "period": PERIOD,
            "kpi": {"content_views": 999_999},
            "usage": [
                {
                    "signal_id": "usage-tenant-b",
                    "name": "dashboard_views",
                    "value": 999,
                    "unit": "events",
                }
            ],
            "incidents": [
                {
                    "incident_id": "incident-tenant-b",
                    "severity": "critical",
                    "status": "open",
                    "title": "Tenant B incident",
                    "impact": "Не должен попасть в отчёт tenant-a",
                }
            ],
        },
    )
    report_response = client.get(
        "/analytics/pilot/reports",
        headers=_headers(subject="council-1", roles=("council",)),
        params={"period": PERIOD},
    )
    kpi_response = client.get(
        "/analytics/kpi",
        headers=_headers(subject="council-1", roles=("council",)),
        params={"period": PERIOD},
    )

    assert collect_response.status_code == 201
    assert collect_response.json() == {
        "batch_id": "pilot-batch-week-26",
        "tenant_id": "tenant-a",
        "period": PERIOD,
        "source": "pilot-observability",
        "kpi_events_recorded": 8,
        "usage_signals_recorded": 2,
        "incidents_recorded": 1,
    }
    assert tenant_b_noise.status_code == 201

    assert kpi_response.status_code == 200
    metrics = {item["key"]: item for item in kpi_response.json()["metrics"]}
    assert metrics["active_members"]["value"] == 20
    assert metrics["content_views"]["value"] == 12_500
    assert metrics["avg_reading_minutes"]["value"] == 4.5
    assert metrics["comments"]["status"] == "on_track"

    assert report_response.status_code == 200
    report = report_response.json()
    assert report["tenant_id"] == "tenant-a"
    assert report["period"] == PERIOD
    assert report["report_frequency"] == "weekly"
    assert report["recipients"] == ["council"]
    assert report["kpi"]["summary"]["metrics_on_track"] == 8
    assert report["aggregates"]["categories"][1]["totals"]["content_viewed"] == 12_500
    assert report["usage"]["signals_total"] == 2
    assert report["usage"]["metrics"] == [
        {
            "name": "dashboard_views",
            "unit": "events",
            "value": 44,
            "signal_count": 1,
            "sources": ["pilot-observability"],
        },
        {
            "name": "voice_submissions",
            "unit": "events",
            "value": 6,
            "signal_count": 1,
            "sources": ["pilot-observability"],
        },
    ]
    assert report["incidents"]["incidents_total"] == 1
    assert report["incidents"]["resolved_incidents"] == 1
    assert report["incidents"]["severity_counts"] == {"medium": 1}
    assert report["feedback_loop"]["status"] == "ready_for_council_review"
    assert report["feedback_loop"]["signals"] == [
        "kpi:on_track",
        "incidents:resolved",
    ]
    assert "999999" not in report_response.text
    assert "incident-tenant-b" not in report_response.text

    state = cast(AnalyticsEngineAPIState, app.state.analytics_engine_api)
    assert len(state.publisher.messages) == 9
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "analytics.event_recorded",
        "analytics.event_recorded",
        "analytics.event_recorded",
        "analytics.event_recorded",
        "analytics.event_recorded",
        "analytics.event_recorded",
        "analytics.event_recorded",
        "analytics.event_recorded",
        "analytics.pilot_usage_recorded",
        "analytics.pilot_usage_recorded",
        "analytics.pilot_incident_recorded",
        "analytics.pilot_batch_collected",
        "analytics.event_recorded",
        "analytics.pilot_usage_recorded",
        "analytics.pilot_incident_recorded",
        "analytics.pilot_batch_collected",
    ]


def test_issue_92_pilot_report_enforces_council_access_and_tenant_context() -> None:
    app = _app()
    client = TestClient(app)

    forbidden = client.get(
        "/analytics/pilot/reports",
        headers=_headers(subject="member-1", roles=("member_full",)),
        params={"period": PERIOD},
    )
    headers = _headers(subject="council-1", roles=("council",))
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.get(
        "/analytics/pilot/reports",
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


def test_issue_92_pilot_telemetry_docs_are_marked_implemented() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = (root / "docs/modules/analytics-engine.md").read_text(encoding="utf-8")
    readme = (root / "services/analytics-engine/README.md").read_text(encoding="utf-8")
    stage = (root / "docs/STAGE_7_ACCEPTANCE.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано для #61 и #92",
        "POST** `/analytics/pilot/telemetry/collect`",
        "GET** `/analytics/pilot/reports?period=`",
        "tenant-isolation контракт #92",
    ):
        assert marker in spec

    for marker in (
        "Сбор KPI и телеметрии пилота #92",
        "POST /analytics/pilot/telemetry/collect",
        "GET /analytics/pilot/reports",
        "analytics.pilot_batch_collected",
    ):
        assert marker in readme

    for marker in (
        "issue #92",
        "KPI и телеметрия собираются автоматически",
        "Отчёты доступны Совету",
        "tenant-isolation контракт #92",
    ):
        assert marker in stage


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
    correlation_id: str = "corr-pilot-telemetry-issue-92",
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
