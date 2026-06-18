from __future__ import annotations

from typing import cast

from cglr import CGLRAPIState, create_cglr_app
from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "cglr-issue-38-secret"


def test_issue_38_cglr_epic_acceptance_contract() -> None:
    app = create_cglr_app(
        ServiceTemplateConfig(
            service_name="cglr",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/generate",
        headers=_headers(),
        json={
            "template_id": "template-issue-38",
            "template_body": "# {{ title }}\n\n{{ body }}\n\nCTA: {{ cta }}",
            "context": {
                "title": "Дайджест НМЦ",
                "body": "Материал готов к публикации.",
                "cta": "Присоединиться",
            },
            "validation": {
                "max_length": 500,
                "required_blocks": ["# Дайджест НМЦ", "CTA:"],
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
                        "owner_id": "partner-low",
                        "url": "https://partners.example/low",
                        "contribution_weight": 9,
                    },
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
                "rotation_seed": "issue-38-acceptance",
            },
            "contribution": {
                "event_type": "content_creation",
                "platform": "telegram",
                "reach": 50_000,
                "metadata": {"issue": "38"},
            },
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["content"] == (
        "# Дайджест НМЦ\n\nМатериал готов к публикации.\n\nCTA: Присоединиться"
    )
    assert "Реферальные ссылки:" in body["content_with_links"]

    links_by_level = {link["level"]: link for link in body["links"]}
    assert set(links_by_level) == {"L1", "L2", "L3"}
    assert links_by_level["L1"]["owner_id"] == "admin-main"
    assert links_by_level["L2"]["owner_id"] == "author-7"
    assert links_by_level["L3"]["owner_id"] in {"partner-a", "partner-b"}
    assert links_by_level["L3"]["owner_id"] != "partner-low"
    assert all("nmc_route_id=" in link["url"] for link in links_by_level.values())

    reward_distribution = {
        item["level"]: item["reward_share"] for item in body["reward_distribution"]
    }
    assert reward_distribution == {"L1": 0.2, "L2": 0.1, "L3": 0.05}

    assert body["contribution"]["source_type"] == "cglr_generation"
    assert body["contribution"]["source_ref"] == body["content_id"]
    assert body["contribution"]["points_awarded"] == 23.4

    state = cast(CGLRAPIState, app.state.cglr_api)
    assert [message.envelope.type for message in state.publisher.messages] == [
        "content.generated",
        "contribution.recorded",
        "audit.record.requested",
    ]


def _headers() -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": "tenant-a",
            "sub": "author-7",
            "roles": ["member_full"],
        },
        JWT_SECRET,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": "tenant-a",
        "X-Correlation-Id": "corr-issue-38",
        "Idempotency-Key": "issue-38-acceptance",
    }
