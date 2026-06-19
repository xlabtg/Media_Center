from __future__ import annotations

from datetime import UTC, datetime

from analytics_engine import (
    AnalyticsCategory,
    AnalyticsEventRecord,
    AnalyticsEventType,
    InMemoryAnalyticsRepository,
    subject_ref_hash,
)
from fastapi import FastAPI
from web_cabinet import create_web_cabinet_app

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "analytics-dashboard-demo-secret"
TENANT_ID = "tenant-a"
BOARD_ID = "board-1"
PERIOD = "2026-W25"
PREVIOUS_PERIOD = "2026-W24"
DEMO_TOKEN = encode_hs256_jwt(
    {
        "tenant_id": TENANT_ID,
        "sub": BOARD_ID,
        "roles": ["board"],
    },
    JWT_SECRET,
)

_EVENT_CATEGORIES = {
    AnalyticsEventType.MEMBER_ACTIVE: AnalyticsCategory.PARTICIPATION,
    AnalyticsEventType.MEMBER_JOINED: AnalyticsCategory.PARTICIPATION,
    AnalyticsEventType.MATERIAL_PUBLISHED: AnalyticsCategory.CONTENT,
    AnalyticsEventType.CONTENT_VIEWED: AnalyticsCategory.CONTENT,
    AnalyticsEventType.READING_TIME_RECORDED: AnalyticsCategory.CONTENT,
    AnalyticsEventType.COMMENT_CREATED: AnalyticsCategory.ENGAGEMENT,
    AnalyticsEventType.TASK_COMPLETED: AnalyticsCategory.ACTIONS,
    AnalyticsEventType.INITIATIVE_CREATED: AnalyticsCategory.ACTIONS,
}


def build_demo_app() -> FastAPI:
    analytics_repository = InMemoryAnalyticsRepository()
    _seed_demo_data(analytics_repository)
    return create_web_cabinet_app(
        ServiceTemplateConfig(
            service_name="web-cabinet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        analytics_repository=analytics_repository,
    )


def demo_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {DEMO_TOKEN}",
        "X-Tenant-Id": TENANT_ID,
        "X-Correlation-Id": "corr-analytics-dashboard-demo",
    }


def _seed_demo_data(repository: InMemoryAnalyticsRepository) -> None:
    for member_index in range(18):
        _add_event(
            repository,
            event_id=f"evt-dashboard-active-{member_index}",
            event_type=AnalyticsEventType.MEMBER_ACTIVE,
            period=PERIOD,
            value=1,
            member_id=f"member-{member_index}",
            occurred_at=datetime(2026, 6, 19, 10, 0, tzinfo=UTC),
        )

    for event_id, event_type, value, sample_count in (
        ("evt-dashboard-new-members", AnalyticsEventType.MEMBER_JOINED, 4, None),
        ("evt-dashboard-materials", AnalyticsEventType.MATERIAL_PUBLISHED, 24, None),
        ("evt-dashboard-views", AnalyticsEventType.CONTENT_VIEWED, 12_500, None),
        (
            "evt-dashboard-reading",
            AnalyticsEventType.READING_TIME_RECORDED,
            24_000,
            100,
        ),
        ("evt-dashboard-comments", AnalyticsEventType.COMMENT_CREATED, 60, None),
        ("evt-dashboard-tasks", AnalyticsEventType.TASK_COMPLETED, 14, None),
        ("evt-dashboard-initiatives", AnalyticsEventType.INITIATIVE_CREATED, 2, None),
    ):
        _add_event(
            repository,
            event_id=event_id,
            event_type=event_type,
            period=PERIOD,
            value=value,
            sample_count=sample_count,
            occurred_at=datetime(2026, 6, 19, 11, 0, tzinfo=UTC),
        )

    for event_id, event_type, value in (
        ("evt-dashboard-prev-members", AnalyticsEventType.MEMBER_ACTIVE, 12),
        ("evt-dashboard-prev-views", AnalyticsEventType.CONTENT_VIEWED, 8_000),
        ("evt-dashboard-prev-comments", AnalyticsEventType.COMMENT_CREATED, 38),
    ):
        _add_event(
            repository,
            event_id=event_id,
            event_type=event_type,
            period=PREVIOUS_PERIOD,
            value=value,
            occurred_at=datetime(2026, 6, 12, 11, 0, tzinfo=UTC),
        )

    _add_event(
        repository,
        event_id="evt-dashboard-tenant-b-views",
        tenant_id="tenant-b",
        event_type=AnalyticsEventType.CONTENT_VIEWED,
        period=PERIOD,
        value=999_999,
        occurred_at=datetime(2026, 6, 19, 11, 5, tzinfo=UTC),
    )


def _add_event(
    repository: InMemoryAnalyticsRepository,
    *,
    event_id: str,
    event_type: AnalyticsEventType,
    period: str,
    value: float,
    occurred_at: datetime,
    tenant_id: str = TENANT_ID,
    member_id: str | None = None,
    sample_count: int | None = None,
) -> None:
    member_hash = (
        subject_ref_hash(tenant_id=tenant_id, subject_id=member_id)
        if member_id is not None
        else None
    )
    repository.add_event(
        AnalyticsEventRecord(
            event_id=event_id,
            tenant_id=tenant_id,
            type=event_type.value,
            category=_EVENT_CATEGORIES[event_type].value,
            period=period,
            value=value,
            sample_count=sample_count,
            member_hash=member_hash,
            audit_hash="a" * 64,
            correlation_id="corr-analytics-dashboard-demo-seed",
            occurred_at=occurred_at,
            metadata={"source": "analytics-dashboard-demo"},
        )
    )


app = build_demo_app()
