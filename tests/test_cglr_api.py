from __future__ import annotations

from typing import cast

from cglr import CGLRAPIState, create_cglr_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "cglr-test-secret"


def _app() -> FastAPI:
    config = ServiceTemplateConfig(
        service_name="cglr",
        version="0.1.0",
        jwt_secret=JWT_SECRET,
        prometheus_enabled=True,
    )
    return create_cglr_app(config)


def _client() -> TestClient:
    return TestClient(_app())


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str = "author-7",
    idempotency_key: str | None = None,
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": ["member_full"],
        },
        JWT_SECRET,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": f"corr-{tenant_id}",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key

    return headers


def _generate_payload() -> dict[str, object]:
    return {
        "template_id": "template-news-1",
        "template_body": "# {{ title }}\n{{ body }}",
        "context": {
            "title": "Новый кооперативный дайджест",
            "body": "Материал готов к публикации.",
        },
        "validation": {
            "max_length": 500,
            "required_blocks": ["# Новый кооперативный дайджест"],
        },
        "platform_targets": ["telegram", "vk"],
        "link_routing": {
            "admin_link": {
                "owner_id": "admin-main",
                "url": "https://nmc.example/join",
            },
            "author_link": {
                "owner_id": "author-7",
                "url": "https://authors.example/author-7",
            },
            "l3_candidates": [
                {
                    "owner_id": "partner-a",
                    "url": "https://partners.example/a",
                    "contribution_weight": 10,
                },
                {
                    "owner_id": "partner-b",
                    "url": "https://partners.example/b",
                    "contribution_weight": 30,
                },
            ],
            "rotation_seed": "campaign-001",
        },
        "contribution": {
            "event_type": "content_creation",
            "platform": "telegram",
            "reach": 100_000,
            "occurred_at": "2026-06-18T12:00:00Z",
            "metadata": {"campaign": "pilot"},
        },
    }


def test_cglr_openapi_documents_generation_endpoints() -> None:
    client = _client()

    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["paths"].keys() >= {
        "/generate",
        "/content/{content_id}",
    }


def test_generation_creates_content_links_and_contribution_record() -> None:
    app = _app()
    client = TestClient(app)

    response = client.post(
        "/generate",
        headers=_headers(idempotency_key="generate-1"),
        json=_generate_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["template_id"] == "template-news-1"
    assert body["content"] == (
        "# Новый кооперативный дайджест\nМатериал готов к публикации."
    )
    assert body["content_with_links"].startswith(body["content"])
    assert "L1: https://nmc.example/join?" in body["content_with_links"]
    assert len(body["content_hash"]) == 64
    assert [link["level"] for link in body["links"]] == ["L1", "L2", "L3"]
    assert [item["reward_share"] for item in body["reward_distribution"]] == [
        0.2,
        0.1,
        0.05,
    ]
    assert body["contribution"]["tenant_id"] == "tenant-a"
    assert body["contribution"]["member_id"] == "author-7"
    assert body["contribution"]["event_type"] == "content_creation"
    assert body["contribution"]["source_type"] == "cglr_generation"
    assert body["contribution"]["source_ref"] == body["content_id"]
    assert body["contribution"]["points_awarded"] == 27.0
    assert len(body["contribution"]["audit_hash"]) == 64

    state = cast(CGLRAPIState, app.state.cglr_api)
    assert [message.routing_key for message in state.publisher.messages] == [
        "tenant.tenant-a.content.generated",
        "tenant.tenant-a.contribution.recorded",
        "tenant.tenant-a.audit.record.requested",
    ]
    content_event = state.publisher.messages[0].envelope
    assert content_event.payload["content_id"] == body["content_id"]
    assert content_event.payload["template_id"] == "template-news-1"
    assert content_event.payload["platform_targets"] == ["telegram", "vk"]
    assert "Новый кооперативный дайджест" not in content_event.to_json()

    fetched = client.get(
        f"/content/{body['content_id']}",
        headers=_headers(),
    )

    assert fetched.status_code == 200
    assert fetched.json() == body


def test_generation_is_idempotent_and_rejects_conflict() -> None:
    app = _app()
    client = TestClient(app)
    headers = _headers(idempotency_key="generate-idempotent")
    payload = _generate_payload()

    first = client.post("/generate", headers=headers, json=payload)
    second = client.post("/generate", headers=headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["content_id"] == first.json()["content_id"]
    state = cast(CGLRAPIState, app.state.cglr_api)
    assert len(state.publisher.messages) == 3

    conflict_payload = {
        **payload,
        "context": {
            "title": "Другой заголовок",
            "body": "Материал готов к публикации.",
        },
    }
    conflict = client.post(
        "/generate",
        headers=headers,
        json=conflict_payload,
    )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_cglr_returns_validation_error_envelope() -> None:
    client = _client()
    payload = _generate_payload()
    payload["link_routing"] = {
        "admin_link": {"owner_id": "admin-main", "url": "https://nmc.example/join"},
        "author_link": {
            "owner_id": "author-7",
            "url": "https://authors.example/author-7",
        },
        "l3_candidates": [
            {
                "owner_id": "partner-low",
                "url": "https://partners.example/low",
                "contribution_weight": 9.99,
            }
        ],
    }

    response = client.post(
        "/generate",
        headers=_headers(idempotency_key="generate-invalid"),
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_cglr_rejects_unsafe_template_context_keys() -> None:
    client = _client()
    payload = _generate_payload()
    payload["context"] = {"__class__": "blocked"}

    response = client.post(
        "/generate",
        headers=_headers(idempotency_key="generate-unsafe-context"),
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_cglr_returns_403_for_tenant_override() -> None:
    app = _app()
    client = TestClient(app)
    headers = _headers(idempotency_key="generate-cross-tenant")
    headers["X-Tenant-Id"] = "tenant-b"

    response = client.post(
        "/generate",
        headers=headers,
        json=_generate_payload(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_isolation_violation"
    state = cast(CGLRAPIState, app.state.cglr_api)
    assert state.audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.audit_sink.events[0].tenant_id == "tenant-a"


def test_cglr_rejects_cross_tenant_content_read() -> None:
    app = _app()
    client = TestClient(app)
    generated = client.post(
        "/generate",
        headers=_headers(idempotency_key="generate-owned"),
        json=_generate_payload(),
    )

    response = client.get(
        f"/content/{generated.json()['content_id']}",
        headers=_headers(tenant_id="tenant-b", subject="author-b"),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_isolation_violation"
    state = cast(CGLRAPIState, app.state.cglr_api)
    assert state.audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.audit_sink.events[0].tenant_id == "tenant-b"
