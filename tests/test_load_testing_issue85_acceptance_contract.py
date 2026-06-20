from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cglr import create_cglr_app
from contribution_ledger import create_contribution_ledger_app
from fastapi.testclient import TestClient
from hitl_payout_gateway import create_hitl_payout_app
from messenger_adapter import (
    BasePlatformAdapter,
    InMemoryPlatformPublisher,
    InMemoryPlatformTokenStore,
    PlatformTokenCipher,
    PublicationBatchRequest,
    UnifiedMessengerAdapter,
)

from libs.shared import (
    LoadTarget,
    LoadTargetEvaluation,
    LoadTestReport,
    ServiceTemplateConfig,
    encode_hs256_jwt,
    run_and_evaluate_load_scenario,
    run_async_and_evaluate_load_scenario,
)

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "tenant-a"
JWT_SECRET = "issue-85-load-secret"


def test_issue85_load_targets_are_reproducible_and_met() -> None:
    scenarios = (
        _run_cglr_load_scenario(),
        _run_ledger_load_scenario(),
        _run_messenger_load_scenario(),
        _run_hitl_load_scenario(),
    )
    report = LoadTestReport(scenarios)

    assert report.passed, report.summary()
    assert {scenario.target.name for scenario in report.evaluations} == {
        "cglr.generate_content",
        "contribution_ledger.record_event",
        "messenger.publish",
        "hitl.queue_and_veto",
    }
    assert report.evaluation_for("cglr.generate_content").result.p95_latency_ms < 200
    assert report.evaluation_for("messenger.publish").result.success_ratio > 0.99


def test_issue85_load_scenarios_are_documented() -> None:
    strategy = (ROOT / "docs/TESTING_STRATEGY.md").read_text(encoding="utf-8")
    load_testing = (ROOT / "docs/LOAD_TESTING.md").read_text(encoding="utf-8")

    required_markers = [
        "#85",
        "tests/test_load_testing_issue85_acceptance_contract.py",
        "experiments/validate_issue85_load_targets.py",
        "CGLR: 100 req/s при p95 < 200 мс",
        "Contribution Ledger: 50 событий/с",
        "Messenger: 200 публикаций/мин при > 99 % успеха",
        "HITL: 10 очередей/ч, veto p95 < 5 с",
        "Узкие места",
    ]
    missing = [marker for marker in required_markers if marker not in load_testing]

    assert not missing
    assert "docs/LOAD_TESTING.md" in strategy


def _run_cglr_load_scenario() -> LoadTargetEvaluation:
    return asyncio.run(_run_cglr_load_scenario_async())


async def _run_cglr_load_scenario_async() -> LoadTargetEvaluation:
    operation_count = 160
    app = create_cglr_app(
        ServiceTemplateConfig(
            service_name="cglr",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )
    headers_by_iteration = tuple(
        _headers(
            subject="author-7",
            roles=("member_full",),
            idempotency_key=f"issue-85-cglr-{iteration}",
            correlation_id=f"corr-issue-85-cglr-{iteration}",
        )
        for iteration in range(operation_count)
    )
    payloads = tuple(
        {
            "template_id": f"template-issue-85-{iteration}",
            "template_body": "# {{ title }}\n\n{{ body }}\n\nCTA: {{ cta }}",
            "context": {
                "title": "Дайджест НМЦ",
                "body": f"Материал {iteration} готов к публикации.",
                "cta": "Присоединиться",
            },
            "validation": {
                "max_length": 500,
                "required_blocks": ["# Дайджест НМЦ", "CTA:"],
            },
            "platform_targets": ["telegram", "vk"],
            "link_routing": _link_routing_payload(iteration),
            "contribution": {
                "event_type": "content_creation",
                "platform": "telegram",
                "reach": 50_000,
                "metadata": {"issue": "85", "iteration": iteration},
            },
        }
        for iteration in range(operation_count)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        warmup_response = await client.post(
            "/generate",
            headers=_headers(
                subject="author-7",
                roles=("member_full",),
                idempotency_key="issue-85-cglr-warmup",
                correlation_id="corr-issue-85-cglr-warmup",
            ),
            json={
                "template_id": "template-issue-85-warmup",
                "template_body": "# {{ title }}\n\n{{ body }}\n\nCTA: {{ cta }}",
                "context": {
                    "title": "Дайджест НМЦ",
                    "body": "Прогрев шаблона перед измерением.",
                    "cta": "Присоединиться",
                },
                "validation": {
                    "max_length": 500,
                    "required_blocks": ["# Дайджест НМЦ", "CTA:"],
                },
                "platform_targets": ["telegram", "vk"],
                "link_routing": _link_routing_payload(-1),
                "contribution": {
                    "event_type": "content_creation",
                    "platform": "telegram",
                    "reach": 50_000,
                    "metadata": {"issue": "85", "iteration": "warmup"},
                },
            },
        )
        assert warmup_response.status_code == 201

        async def operation(iteration: int) -> bool:
            response = await client.post(
                "/generate",
                headers=headers_by_iteration[iteration],
                json=payloads[iteration],
            )
            return response.status_code == 201

        return await run_async_and_evaluate_load_scenario(
            LoadTarget(
                name="cglr.generate_content",
                operation_count=operation_count,
                min_throughput_per_second=100,
                max_p95_latency_ms=200,
                min_success_ratio=1,
            ),
            operation,
            max_concurrency=4,
        )


def _run_ledger_load_scenario() -> LoadTargetEvaluation:
    operation_count = 80
    app = create_contribution_ledger_app(
        ServiceTemplateConfig(
            service_name="contribution-ledger",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )
    headers_by_iteration = tuple(
        _headers(
            subject=f"member-{iteration % 12}",
            roles=("member_full",),
            idempotency_key=f"issue-85-ledger-{iteration}",
        )
        for iteration in range(operation_count)
    )
    payloads = tuple(
        {
            "member_id": f"member-{iteration % 12}",
            "event_type": "publish",
            "source_type": "publication",
            "source_ref": f"publication-issue-85-{iteration}",
            "platform": "telegram",
            "reach": 2_000 + iteration,
            "occurred_at": "2026-06-20T12:00:00Z",
            "metadata": {"issue": "85", "iteration": iteration},
        }
        for iteration in range(operation_count)
    )

    with TestClient(app) as client:

        def operation(iteration: int) -> bool:
            response = client.post(
                "/contributions",
                headers=headers_by_iteration[iteration],
                json=payloads[iteration],
            )
            return bool(response.status_code == 201)

        return run_and_evaluate_load_scenario(
            LoadTarget(
                name="contribution_ledger.record_event",
                operation_count=operation_count,
                min_throughput_per_second=50,
                min_success_ratio=1,
            ),
            operation,
        )


def _run_messenger_load_scenario() -> LoadTargetEvaluation:
    publisher = InMemoryPlatformPublisher(connector_name="telegram_mock")
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    token_store.save_token(
        tenant_id=TENANT_ID,
        platform="telegram",
        token="tg-secret-token",
    )
    adapter = UnifiedMessengerAdapter(
        platform_adapters={
            "telegram": BasePlatformAdapter(
                platform="telegram",
                publisher=publisher,
                token_store=token_store,
                sleeper=lambda delay: None,
            )
        }
    )

    def operation(iteration: int) -> bool:
        result = asyncio.run(
            adapter.publish(
                PublicationBatchRequest(
                    tenant_id=TENANT_ID,
                    publication_id=f"pub-issue-85-{iteration}",
                    platforms=("telegram",),
                    target_ids={"telegram": "@nmc_channel"},
                    content=f"Готовый материал для публикации {iteration}",
                    correlation_id=f"corr-issue-85-{iteration}",
                    metadata={"issue": "85"},
                )
            )
        )
        return result.failed == () and result.succeeded_platforms == ("telegram",)

    return run_and_evaluate_load_scenario(
        LoadTarget(
            name="messenger.publish",
            operation_count=100,
            min_throughput_per_second=200 / 60,
            min_success_ratio=0.9901,
        ),
        operation,
    )


def _run_hitl_load_scenario() -> LoadTargetEvaluation:
    client = TestClient(
        create_hitl_payout_app(
            ServiceTemplateConfig(
                service_name="hitl-payout-gateway",
                version="0.1.0",
                jwt_secret=JWT_SECRET,
                prometheus_enabled=True,
            ),
            veto_window_hours=8,
        )
    )
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)

    def operation(iteration: int) -> bool:
        payout_id = f"payout-issue-85-{iteration}"
        queued = client.post(
            "/payouts/queue",
            headers=_headers(
                subject="council-1",
                roles=("council",),
                correlation_id=f"corr-issue-85-hitl-{iteration}",
            ),
            json={
                "payout_id": payout_id,
                "event_id": f"evt-{payout_id}-queued",
                "member_id": f"member-{iteration % 12}",
                "period": "2026-06",
                "payout_share": 0.1,
                "distribution_id": "distribution-issue-85",
                "distribution_hash": "a" * 64,
                "now": now.isoformat().replace("+00:00", "Z"),
            },
        )
        vetoed = client.post(
            f"/payouts/{payout_id}/veto",
            headers=_headers(
                subject="council-2",
                roles=("council",),
                correlation_id=f"corr-issue-85-veto-{iteration}",
            ),
            json={
                "decision_id": f"veto-issue-85-{iteration}",
                "event_id": f"evt-{payout_id}-vetoed",
                "reason_code": "policy_mismatch",
                "reason": "Нужна дополнительная проверка нагрузки HITL",
                "now": (now + timedelta(minutes=5)).isoformat(),
            },
        )
        return bool(queued.status_code == 201 and vetoed.status_code == 200)

    return run_and_evaluate_load_scenario(
        LoadTarget(
            name="hitl.queue_and_veto",
            operation_count=12,
            min_throughput_per_second=10 / 3600,
            max_p95_latency_ms=5_000,
            min_success_ratio=1,
        ),
        operation,
    )


def _headers(
    *,
    subject: str,
    roles: tuple[str, ...],
    idempotency_key: str | None = None,
    correlation_id: str = "corr-issue-85",
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": TENANT_ID,
            "sub": subject,
            "roles": list(roles),
        },
        JWT_SECRET,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": TENANT_ID,
        "X-Correlation-Id": correlation_id,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _link_routing_payload(iteration: int) -> dict[str, object]:
    return {
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
                "contribution_weight": 10 + iteration,
            },
            {
                "owner_id": "partner-b",
                "url": "https://partners.example/b",
                "contribution_weight": 30,
            },
        ],
        "rotation_seed": f"issue-85-{iteration}",
    }


def _encryption_key() -> str:
    return base64.b64encode(b"issue-85-load-key-32-byte-value!"[:32]).decode("ascii")
