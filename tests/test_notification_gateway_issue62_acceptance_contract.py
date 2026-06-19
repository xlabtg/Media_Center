from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from notification_gateway import (
    NotificationGatewayAPIState,
    create_notification_gateway_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "notification-gateway-issue-62-secret"


def test_issue_62_notification_gateway_delivers_events_by_preferences() -> None:
    app = _app()
    client = TestClient(app)

    member_preferences = client.put(
        "/notify/preferences",
        headers=_headers(subject="member-1", roles=("member_full",)),
        json={
            "channels": ["telegram", "email", "telegram"],
            "event_types": ["payout.queued"],
            "template_overrides": {"payout.queued": "payout_council_alert"},
        },
    )
    council_preferences = client.put(
        "/notify/preferences",
        headers=_headers(subject="council-1", roles=("council",)),
        json={
            "recipient_id": "council-queue",
            "channels": ["webhook"],
            "event_types": ["payout.queued"],
            "template_overrides": {"payout.queued": "payout_council_alert"},
        },
    )
    dispatch = client.post(
        "/notify",
        headers=_headers(
            subject="board-1",
            roles=("board",),
            correlation_id="corr-notify-payout",
        ),
        json={
            "event_id": "evt-payout-issue-62",
            "event_type": "payout.queued",
            "source": "hitl-payout-gateway",
            "recipients": ["member-1", "council-queue"],
            "channels": ["telegram", "email", "webhook"],
            "template": {
                "template_key": "payout_council_alert",
                "subject_template": "Выплата {{ payout_id }} ждёт решения",
                "body_template": (
                    "Tenant {{ tenant_id }}: {{ member_hash }} получил долю "
                    "{{ payout_share }}"
                ),
                "channels": ["telegram", "email", "webhook"],
            },
            "context": {
                "payout_id": "payout-issue-62",
                "member_hash": "sha256:" + "a" * 64,
                "payout_share": "0.625",
            },
            "priority": "urgent",
            "occurred_at": "2026-06-19T12:00:00Z",
            "metadata": {"issue": "62"},
        },
    )

    assert member_preferences.status_code == 200
    assert member_preferences.json()["channels"] == ["telegram", "email"]
    assert council_preferences.status_code == 200
    assert dispatch.status_code == 202

    body = dispatch.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["event_id"] == "evt-payout-issue-62"
    assert body["event_type"] == "payout.queued"
    assert body["template_key"] == "payout_council_alert"
    assert body["delivered_count"] == 3
    assert body["skipped_count"] == 0
    assert body["failed_count"] == 0
    assert {
        (delivery["recipient_id"], delivery["channel"])
        for delivery in body["deliveries"]
    } == {
        ("member-1", "telegram"),
        ("member-1", "email"),
        ("council-queue", "webhook"),
    }
    assert {delivery["subject"] for delivery in body["deliveries"]} == {
        "Выплата payout-issue-62 ждёт решения"
    }
    assert all(len(delivery["audit_hash"]) == 64 for delivery in body["deliveries"])

    state = cast(NotificationGatewayAPIState, app.state.notification_gateway_api)
    assert [delivery.channel for delivery in state.channel.deliveries] == [
        "telegram",
        "email",
        "webhook",
    ]
    assert [delivery.recipient_id for delivery in state.channel.deliveries] == [
        "member-1",
        "member-1",
        "council-queue",
    ]
    published = state.publisher.messages[-1].envelope
    assert published.type == "notification.dispatched"
    assert published.payload["delivered_count"] == 3
    assert "Выплата payout-issue-62" not in published.to_json()
    assert "payout_share" not in published.to_json()


def test_issue_62_preferences_are_tenant_scoped_and_guarded() -> None:
    app = _app()
    client = TestClient(app)

    tenant_a = client.put(
        "/notify/preferences",
        headers=_headers(subject="member-1", roles=("member_full",)),
        json={"channels": ["telegram"], "event_types": ["contribution.recorded"]},
    )
    tenant_b = client.put(
        "/notify/preferences",
        headers=_headers(
            tenant_id="tenant-b",
            subject="member-1",
            roles=("member_full",),
            correlation_id="corr-tenant-b-notify",
        ),
        json={"channels": ["email"], "event_types": ["payout.queued"]},
    )
    tenant_a_preferences = client.get(
        "/notify/preferences",
        headers=_headers(subject="member-1", roles=("member_full",)),
    )
    headers = _headers(subject="member-1", roles=("member_full",))
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.get("/notify/preferences", headers=headers)

    assert tenant_a.status_code == 200
    assert tenant_b.status_code == 200
    assert tenant_a_preferences.status_code == 200
    assert tenant_a_preferences.json()["tenant_id"] == "tenant-a"
    assert tenant_a_preferences.json()["channels"] == ["telegram"]
    assert tenant_a_preferences.json()["event_types"] == ["contribution.recorded"]

    assert tenant_override.status_code == 403
    assert tenant_override.json()["error"]["code"] == "tenant_isolation_violation"
    state = cast(NotificationGatewayAPIState, app.state.notification_gateway_api)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"


def test_issue_62_notification_gateway_docs_are_marked_implemented() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = (root / "docs/modules/notification-gateway.md").read_text(encoding="utf-8")
    readme = (root / "services/notification-gateway/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "реализовано для #62",
        "POST** `/notify`",
        "GET/PUT** `/notify/preferences`",
        "NotificationGateway",
        "tenant-isolation контракт #62",
    ):
        assert marker in spec

    for marker in (
        "create_notification_gateway_app",
        "POST /notify",
        "GET /notify/preferences",
        "InMemoryNotificationRepository",
    ):
        assert marker in readme


def _app() -> FastAPI:
    return create_notification_gateway_app(
        ServiceTemplateConfig(
            service_name="notification-gateway",
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
    correlation_id: str = "corr-notification-issue-62",
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
