from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from neuro_agent_orchestrator import (
    NeuroAgentOrchestratorAPIState,
    create_neuro_agent_orchestrator_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "neuro-agent-issue-57-secret"


def test_issue_57_proxy_rotation_health_and_tenant_isolation_contract() -> None:
    app = create_neuro_agent_orchestrator_app(
        ServiceTemplateConfig(
            service_name="neuro-agent-orchestrator",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )
    client = TestClient(app)

    tenant_a_headers = _headers(
        subject="board-1",
        roles=("board",),
        tenant_id="tenant-a",
        correlation_id="corr-issue-57-tenant-a",
    )
    created_pool = client.put(
        "/proxy-pools/platform-publishing",
        headers=tenant_a_headers,
        json={
            "event_id": "evt-issue-57-proxy-pool-updated",
            "platform": "telegram",
            "proxies": [
                {
                    "proxy_id": "http-primary",
                    "protocol": "http",
                    "url": "http://http-proxy-a.internal:8080",
                    "priority": 10,
                },
                {
                    "proxy_id": "socks-backup",
                    "protocol": "socks5",
                    "url": "socks5://socks-proxy-a.internal:1080",
                    "priority": 20,
                },
                {
                    "proxy_id": "mtproto-backup",
                    "protocol": "mtproto",
                    "url": "mtproto://mtproto-proxy-a.internal:443",
                    "secret_ref": "vault://tenant-a/proxies/mtproto-backup",
                    "priority": 30,
                },
            ],
            "metadata": {"issue": "57"},
        },
    )
    first_lease = client.post(
        "/proxy-pools/platform-publishing/lease",
        headers=tenant_a_headers,
        json={"event_id": "evt-issue-57-lease-1"},
    )
    second_lease = client.post(
        "/proxy-pools/platform-publishing/lease",
        headers=tenant_a_headers,
        json={"event_id": "evt-issue-57-lease-2"},
    )
    failed_health = client.post(
        "/proxy-pools/platform-publishing/health-checks",
        headers=tenant_a_headers,
        json={
            "event_id": "evt-issue-57-health-check",
            "checks": [
                {
                    "proxy_id": "http-primary",
                    "alive": False,
                    "checked_at": "2026-06-19T04:00:00Z",
                    "reason_code": "tcp_timeout",
                },
                {
                    "proxy_id": "socks-backup",
                    "alive": False,
                    "checked_at": "2026-06-19T04:00:00Z",
                    "reason_code": "connection_refused",
                },
            ],
        },
    )
    failover_lease = client.post(
        "/proxy-pools/platform-publishing/lease",
        headers=tenant_a_headers,
        json={"event_id": "evt-issue-57-lease-failover"},
    )
    repeated_failover_lease = client.post(
        "/proxy-pools/platform-publishing/lease",
        headers=tenant_a_headers,
        json={"event_id": "evt-issue-57-lease-failover-repeat"},
    )

    tenant_b_headers = _headers(
        subject="board-2",
        roles=("board",),
        tenant_id="tenant-b",
        correlation_id="corr-issue-57-tenant-b",
    )
    tenant_b_pool = client.put(
        "/proxy-pools/platform-publishing",
        headers=tenant_b_headers,
        json={
            "platform": "vk",
            "proxies": [
                {
                    "proxy_id": "tenant-b-http",
                    "protocol": "http",
                    "url": "http://http-proxy-b.internal:8080",
                }
            ],
        },
    )
    tenant_a_snapshot = client.get(
        "/proxy-pools/platform-publishing",
        headers=tenant_a_headers,
    )
    tenant_b_snapshot = client.get(
        "/proxy-pools/platform-publishing",
        headers=tenant_b_headers,
    )

    assert created_pool.status_code == 200
    created_body = created_pool.json()
    assert created_body["tenant_id"] == "tenant-a"
    assert created_body["healthy_proxy_count"] == 3
    assert created_body["unhealthy_proxy_count"] == 0
    assert [proxy["protocol"] for proxy in created_body["proxies"]] == [
        "http",
        "socks5",
        "mtproto",
    ]
    assert "vault://tenant-a/proxies/mtproto-backup" not in created_pool.text

    assert first_lease.status_code == 201
    assert first_lease.json()["proxy_id"] == "http-primary"
    assert first_lease.json()["protocol"] == "http"
    assert second_lease.status_code == 201
    assert second_lease.json()["proxy_id"] == "socks-backup"
    assert second_lease.json()["protocol"] == "socks5"

    assert failed_health.status_code == 200
    assert failed_health.json()["healthy_proxy_count"] == 1
    assert failed_health.json()["unhealthy_proxy_count"] == 2
    assert [
        (proxy["proxy_id"], proxy["health_status"])
        for proxy in failed_health.json()["pool"]["proxies"]
    ] == [
        ("http-primary", "unhealthy"),
        ("socks-backup", "unhealthy"),
        ("mtproto-backup", "healthy"),
    ]

    assert failover_lease.status_code == 201
    assert failover_lease.json()["proxy_id"] == "mtproto-backup"
    assert failover_lease.json()["protocol"] == "mtproto"
    assert repeated_failover_lease.status_code == 201
    assert repeated_failover_lease.json()["proxy_id"] == "mtproto-backup"
    assert "http-primary" not in {
        failover_lease.json()["proxy_id"],
        repeated_failover_lease.json()["proxy_id"],
    }
    assert "socks-backup" not in {
        failover_lease.json()["proxy_id"],
        repeated_failover_lease.json()["proxy_id"],
    }

    assert tenant_b_pool.status_code == 200
    assert tenant_a_snapshot.status_code == 200
    assert tenant_b_snapshot.status_code == 200
    assert [proxy["proxy_id"] for proxy in tenant_a_snapshot.json()["proxies"]] == [
        "http-primary",
        "socks-backup",
        "mtproto-backup",
    ]
    assert [proxy["proxy_id"] for proxy in tenant_b_snapshot.json()["proxies"]] == [
        "tenant-b-http"
    ]
    assert tenant_a_snapshot.json()["tenant_id"] == "tenant-a"
    assert tenant_b_snapshot.json()["tenant_id"] == "tenant-b"

    state = cast(NeuroAgentOrchestratorAPIState, app.state.neuro_agent_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "neuro_agent.proxy_pool.updated",
        "neuro_agent.proxy.leased",
        "neuro_agent.proxy.leased",
        "neuro_agent.proxy_health.checked",
        "neuro_agent.proxy.leased",
        "neuro_agent.proxy.leased",
        "neuro_agent.proxy_pool.updated",
    ]
    audit_json = "".join(
        record.model_dump_json() for record in state.audit_log_sink.records
    )
    assert "vault://tenant-a/proxies/mtproto-backup" not in audit_json
    assert "http-proxy-a.internal" not in audit_json
    assert "socks-proxy-a.internal" not in audit_json
    assert "mtproto-proxy-a.internal" not in audit_json


def test_issue_57_proxy_rotation_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/neuro-agent-orchestrator.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "services/neuro-agent-orchestrator/README.md").read_text(
        encoding="utf-8"
    )
    security = (ROOT / "docs/SECURITY.md").read_text(encoding="utf-8")

    for marker in (
        "#57",
        "ProxyProtocol",
        "ProxyPoolState",
        "/proxy-pools/{pool_id}/lease",
        "HTTP/SOCKS5/MTProto",
    ):
        assert marker in spec

    for marker in (
        "Прокси-ротация поддерживает HTTP, SOCKS5 и MTProto",
        "Неживые прокси исключаются из выдачи",
        "Прокси-пулы изолированы по tenant_id",
    ):
        assert marker in readme

    for marker in (
        "Прокси-ротация",
        "secret_ref",
        "tenant-scoped proxy pools",
    ):
        assert marker in security


def _headers(
    *,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str,
    correlation_id: str,
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
