from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from notification_gateway import (
    EmailMessagePurpose,
    EmailOutboxStatus,
    EmailProviderKind,
    EmailProviderRoute,
    EmailProviderStatus,
    InMemoryEmailProviderAdapter,
    NotificationGatewayAPIState,
    create_notification_gateway_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "notification-gateway-issue-297-secret"


def test_issue_297_email_outbox_contract_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = (root / "docs/modules/notification-gateway.md").read_text(encoding="utf-8")
    readme = (root / "services/notification-gateway/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "Email outbox и provider routing (#297)",
        "message_purpose",
        "metadata.email_recipients",
        "EmailProviderRoute",
        "EmailProviderAdapter",
    ):
        assert marker in spec

    for marker in (
        "Email-доставка для #297",
        "InMemoryEmailOutboxRepository",
        "EmailDeliveryService",
        "InMemoryEmailProviderAdapter",
        "sender_alias",
    ):
        assert marker in readme


def test_issue_297_email_outbox_routes_system_messages_by_active_provider() -> None:
    adapter = InMemoryEmailProviderAdapter(connector_name="postalserver")
    app = _app(email_adapters={"postal-primary": adapter})
    client = TestClient(app)
    state = cast(NotificationGatewayAPIState, app.state.notification_gateway_api)
    state.email_outbox.upsert_route(
        EmailProviderRoute(
            tenant_id="tenant-a",
            route_key="system-primary",
            purpose=EmailMessagePurpose.SYSTEM,
            provider_name="postal-primary",
            provider_kind=EmailProviderKind.POSTALSERVER,
            sender_alias="example.org",
            priority=10,
            status=EmailProviderStatus.ACTIVE,
            endpoint_url="https://postal.example.test/api/send",
            credentials_ref="vault:notification/postal-primary",
        )
    )
    preferences = client.put(
        "/notify/preferences",
        headers=_headers(subject="board-1", roles=("board",)),
        json={
            "recipient_id": "member-1",
            "channels": ["email"],
            "event_types": ["auth.registration_otp_requested"],
        },
    )

    response = client.post(
        "/notify",
        headers=_headers(subject="board-1", roles=("board",)),
        json={
            "event_id": "evt-registration-email-297",
            "event_type": "auth.registration_otp_requested",
            "source": "auth-service",
            "recipients": ["member-1"],
            "channels": ["email"],
            "message_purpose": "system",
            "template": {
                "template_key": "registration_email_otp",
                "subject_template": "Ваш код входа",
                "body_template": "Код подтверждения: {{ otp_code }}",
                "channels": ["email"],
            },
            "context": {"otp_code": "123456"},
            "occurred_at": "2026-06-23T12:00:00Z",
            "metadata": {
                "email_recipients": {"member-1": "member@example.org"},
                "issue": "297",
            },
        },
    )

    assert preferences.status_code == 200
    assert response.status_code == 202
    assert response.json()["delivered_count"] == 1

    outbox_messages = state.email_outbox.list_messages(tenant_id="tenant-a")
    assert len(outbox_messages) == 1
    message = outbox_messages[0]
    assert message.status is EmailOutboxStatus.SENT
    assert message.purpose is EmailMessagePurpose.SYSTEM
    assert message.provider_name == "postal-primary"
    assert message.route_key == "system-primary"
    assert message.sender_alias == "example.org"
    assert message.to_address == "member@example.org"
    assert message.subject == "Ваш код входа"
    assert message.body == "Код подтверждения: 123456"
    assert message.provider_ref_hash is not None

    assert len(adapter.commands) == 1
    command = adapter.commands[0]
    assert command.provider_kind is EmailProviderKind.POSTALSERVER
    assert command.sender_alias == "example.org"
    assert command.credentials_ref == "vault:notification/postal-primary"
    assert command.to_address == "member@example.org"


def test_issue_297_disabled_marketing_route_keeps_message_in_outbox() -> None:
    adapter = InMemoryEmailProviderAdapter(connector_name="mailgun")
    app = _app(email_adapters={"mailgun-eu": adapter})
    client = TestClient(app)
    state = cast(NotificationGatewayAPIState, app.state.notification_gateway_api)
    state.email_outbox.upsert_route(
        EmailProviderRoute(
            tenant_id="tenant-a",
            route_key="marketing-paused",
            purpose=EmailMessagePurpose.MARKETING,
            provider_name="mailgun-eu",
            provider_kind=EmailProviderKind.MAILGUN,
            sender_alias="promo.example.org",
            priority=10,
            status=EmailProviderStatus.DISABLED,
            endpoint_url="https://api.eu.mailgun.net/v3/example.org/messages",
            credentials_ref="vault:notification/mailgun-eu",
        )
    )
    preferences = client.put(
        "/notify/preferences",
        headers=_headers(subject="board-1", roles=("board",)),
        json={
            "recipient_id": "member-1",
            "channels": ["email"],
            "event_types": ["marketing.digest_requested"],
        },
    )

    response = client.post(
        "/notify",
        headers=_headers(subject="board-1", roles=("board",)),
        json={
            "event_id": "evt-marketing-email-297",
            "event_type": "marketing.digest_requested",
            "source": "marketing-service",
            "recipients": ["member-1"],
            "channels": ["email"],
            "message_purpose": "marketing",
            "template": {
                "template_key": "marketing_digest",
                "subject_template": "Дайджест недели",
                "body_template": "Новые материалы НМЦ",
                "channels": ["email"],
            },
            "metadata": {
                "email_recipients": {"member-1": "member@example.org"},
                "issue": "297",
            },
        },
    )

    assert preferences.status_code == 200
    assert response.status_code == 202
    assert response.json()["delivered_count"] == 1

    outbox_messages = state.email_outbox.list_messages(tenant_id="tenant-a")
    assert len(outbox_messages) == 1
    message = outbox_messages[0]
    assert message.status is EmailOutboxStatus.DEFERRED
    assert message.purpose is EmailMessagePurpose.MARKETING
    assert message.last_error_code == "email_route_unavailable"
    assert message.provider_name is None
    assert len(adapter.commands) == 0


def _app(
    *,
    email_adapters: dict[str, InMemoryEmailProviderAdapter] | None = None,
) -> FastAPI:
    return create_notification_gateway_app(
        ServiceTemplateConfig(
            service_name="notification-gateway",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        email_adapters=email_adapters,
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str = "corr-notification-issue-297",
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
