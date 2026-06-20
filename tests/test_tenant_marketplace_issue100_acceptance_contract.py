from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from libs.shared import (
    InMemoryTenantMarketplace,
    InMemoryTenantResourceManager,
    TenantContext,
    TenantMarketplaceApplicationStatus,
    TenantMarketplaceDecision,
    TenantMarketplaceProfile,
    TenantMarketplaceProfileStatus,
    TenantMarketplaceSubmission,
    TenantResourcePlan,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def test_issue100_catalog_lists_only_moderated_public_tenant_profiles() -> None:
    marketplace = InMemoryTenantMarketplace()
    marketplace.publish_profile(
        TenantMarketplaceProfile(
            tenant_id="tenant-pilot",
            slug="nmc-pilot",
            name="НМЦ pilot tenant",
            region="RU-MOW",
            cooperative_type="media",
            description="Пилотный кооператив для проверки НМЦ.",
            member_count_range="15-25",
            capabilities=("content", "analytics", "hitl"),
            status=TenantMarketplaceProfileStatus.PUBLISHED,
            published_at=NOW,
            contact_ref="vault://tenant-pilot/contacts/private",
        ),
    )
    marketplace.publish_profile(
        TenantMarketplaceProfile(
            tenant_id="tenant-hidden",
            slug="hidden-coop",
            name="Скрытый кооператив",
            region="RU-SPE",
            cooperative_type="education",
            description="Профиль не должен попадать в каталог.",
            member_count_range="5-10",
            capabilities=("onboarding",),
            status=TenantMarketplaceProfileStatus.HIDDEN,
            published_at=NOW,
            contact_ref="vault://tenant-hidden/contacts/private",
        ),
    )

    draft = marketplace.submit_application(
        TenantMarketplaceSubmission(
            slug="candidate-coop",
            name="Кандидат в каталог",
            region="RU-NVS",
            cooperative_type="community_media",
            description="Заявка проходит модерацию и пока не опубликована.",
            expected_members=18,
            capabilities=("content", "messenger"),
            contact_ref="vault://applications/candidate-coop/contact",
            requested_plan=_growth_plan(),
            checklist={
                "profile": True,
                "contacts": True,
                "data_policy": True,
                "council_review": False,
            },
        ),
        applicant=TenantContext(
            tenant_id="platform",
            subject="candidate-founder",
            roles=("audience",),
        ),
        submitted_at=NOW,
    )

    catalog = marketplace.list_catalog()
    public_payload = catalog[0].as_public_dict()

    assert draft.status is TenantMarketplaceApplicationStatus.SUBMITTED
    assert [profile.slug for profile in catalog] == ["nmc-pilot"]
    assert catalog[0].status is TenantMarketplaceProfileStatus.PUBLISHED
    assert public_payload["tenant_id"] == "tenant-pilot"
    assert public_payload["slug"] == "nmc-pilot"
    assert "contact_ref" not in public_payload
    assert "vault://" not in str(public_payload)


def test_issue100_self_service_application_provisions_tenant_after_moderation() -> None:
    resource_manager = InMemoryTenantResourceManager(
        default_plan=TenantResourcePlan(
            name="default",
            request_limit=20,
            window_seconds=60,
            concurrent_operations=4,
            storage_bytes=4_096,
            queue_depth=4,
        ),
    )
    marketplace = InMemoryTenantMarketplace(resource_manager=resource_manager)
    application = marketplace.submit_application(
        TenantMarketplaceSubmission(
            slug="coop-alpha",
            name="Кооператив Альфа",
            region="RU-MOW",
            cooperative_type="media",
            description="Самостоятельная заявка кооператива на подключение.",
            expected_members=22,
            capabilities=("content", "analytics", "wallet"),
            contact_ref="vault://applications/coop-alpha/contact",
            requested_plan=_growth_plan(),
            checklist={
                "profile": True,
                "contacts": True,
                "data_policy": True,
                "council_review": True,
            },
        ),
        applicant=TenantContext(
            tenant_id="platform",
            subject="founder-alpha",
            roles=("audience",),
        ),
        submitted_at=NOW,
    )

    provisioning = marketplace.moderate_application(
        application.application_id,
        decision=TenantMarketplaceDecision.APPROVE,
        reviewer=TenantContext(
            tenant_id="platform",
            subject="council-1",
            roles=("council",),
        ),
        decided_at=NOW,
        comment="Профиль, контакты и политика данных проверены.",
        tenant_id="tenant-coop-alpha",
    )

    catalog = marketplace.list_catalog(region="RU-MOW", cooperative_type="media")
    snapshot = resource_manager.snapshot(
        TenantContext(tenant_id="tenant-coop-alpha", subject="auditor"),
    )

    assert (
        provisioning.application.status
        is TenantMarketplaceApplicationStatus.PROVISIONED
    )
    assert provisioning.tenant_id == "tenant-coop-alpha"
    assert provisioning.profile.slug == "coop-alpha"
    assert provisioning.profile.resource_plan_name == "growth-stage8"
    assert provisioning.application.moderation_history[-1].decision is (
        TenantMarketplaceDecision.APPROVE
    )
    assert [profile.slug for profile in catalog] == ["coop-alpha"]
    assert snapshot.plan_name == "growth-stage8"
    assert snapshot.request_limit == 120


def test_issue100_moderation_blocks_incomplete_or_duplicate_applications() -> None:
    marketplace = InMemoryTenantMarketplace()
    marketplace.publish_profile(
        TenantMarketplaceProfile(
            tenant_id="tenant-existing",
            slug="existing-coop",
            name="Существующий кооператив",
            region="RU-MOW",
            cooperative_type="media",
            description="Профиль уже опубликован.",
            member_count_range="15-25",
            capabilities=("content",),
            status=TenantMarketplaceProfileStatus.PUBLISHED,
            published_at=NOW,
        ),
    )
    incomplete = marketplace.submit_application(
        TenantMarketplaceSubmission(
            slug="new-coop",
            name="Неполная заявка",
            region="RU-MOW",
            cooperative_type="media",
            description="Нет подтвержденной политики данных.",
            expected_members=12,
            capabilities=("content",),
            contact_ref="vault://applications/new-coop/contact",
            requested_plan=_growth_plan(),
            checklist={
                "profile": True,
                "contacts": True,
                "data_policy": False,
                "council_review": True,
            },
        ),
        applicant=TenantContext(
            tenant_id="platform",
            subject="founder-new",
            roles=("audience",),
        ),
        submitted_at=NOW,
    )

    changes = marketplace.moderate_application(
        incomplete.application_id,
        decision=TenantMarketplaceDecision.REQUEST_CHANGES,
        reviewer=TenantContext(
            tenant_id="platform",
            subject="board-1",
            roles=("board",),
        ),
        decided_at=NOW,
        comment="Нужна подтвержденная политика данных.",
    )
    duplicate = TenantMarketplaceSubmission(
        slug="existing-coop",
        name="Дубликат",
        region="RU-MOW",
        cooperative_type="media",
        description="Slug уже занят опубликованным tenant.",
        expected_members=16,
        capabilities=("content",),
        contact_ref="vault://applications/existing-coop/contact",
        requested_plan=_growth_plan(),
        checklist={
            "profile": True,
            "contacts": True,
            "data_policy": True,
            "council_review": True,
        },
    )

    assert changes.status is TenantMarketplaceApplicationStatus.NEEDS_CHANGES
    assert changes.ready_for_moderation is False
    with pytest.raises(ValueError, match="slug уже занят"):
        marketplace.submit_application(
            duplicate,
            applicant=TenantContext(
                tenant_id="platform",
                subject="founder-duplicate",
                roles=("audience",),
            ),
            submitted_at=NOW,
        )


def test_issue100_tenant_marketplace_contract_is_documented() -> None:
    marketplace_doc = (ROOT / "docs/TENANT_MARKETPLACE.md").read_text(
        encoding="utf-8",
    )
    data_model = (ROOT / "docs/DATA_MODEL.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    scaling_doc = (ROOT / "docs/MULTITENANT_SCALING.md").read_text(
        encoding="utf-8",
    )

    for marker in (
        "Статус: baseline для issue #100",
        "GET /tenants/catalog",
        "POST /tenants/applications",
        "TenantMarketplaceSubmission",
        "TenantMarketplaceDecision",
        "InMemoryTenantMarketplace",
        "resource_plan",
        "moderation",
        "no_pdn_no_secrets",
        "tests/test_tenant_marketplace_issue100_acceptance_contract.py",
    ):
        assert marker in marketplace_doc

    assert "tenant_marketplace_profiles" in data_model
    assert "tenant_onboarding_applications" in data_model
    assert "Tenant Marketplace" in architecture
    assert "issue #100" in scaling_doc


def _growth_plan() -> TenantResourcePlan:
    return TenantResourcePlan(
        name="growth-stage8",
        request_limit=120,
        window_seconds=60,
        concurrent_operations=12,
        storage_bytes=262_144,
        queue_depth=64,
    )
