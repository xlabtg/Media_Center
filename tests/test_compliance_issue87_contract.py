from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from web_cabinet import (
    CabinetContentRecord,
    CabinetContributionRecord,
    CabinetReferralLink,
    InMemoryWebCabinetRepository,
    OnboardingConsentRecord,
    OnboardingProfileRecord,
    OnboardingStepRecord,
    WebCabinetAPIState,
    create_web_cabinet_app,
)

from libs.shared import ServiceTemplateConfig, TenantContext, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "compliance-issue-87-secret"


def test_issue_87_fz152_checklist_data_map_and_consents_are_traceable() -> None:
    repository = InMemoryWebCabinetRepository()
    _seed_privacy_projection(repository=repository, tenant_id="tenant-a")
    client = TestClient(_app(repository=repository))

    checklist = client.get(
        "/compliance/fz152/checklist",
        headers=_headers(subject="council-1", roles=("council",)),
    )
    data_map = client.get(
        "/privacy/data-map",
        headers=_headers(subject="council-1", roles=("council",)),
    )
    consents = client.get(
        "/privacy/consents",
        headers=_headers(subject="member-a", roles=("member_full",)),
    )

    assert checklist.status_code == 200
    checklist_body = checklist.json()
    assert checklist_body["tenant_id"] == "tenant-a"
    assert checklist_body["passed"] is True
    assert {item["item_id"] for item in checklist_body["items"]} >= {
        "pdn_operator_scope",
        "consent_registry",
        "data_minimization",
        "data_subject_requests",
        "voice_raw_audio_ttl",
        "hash_only_audit_chain",
    }
    assert all(item["status"] == "passed" for item in checklist_body["items"])

    assert data_map.status_code == 200
    data_map_categories = {item["category"] for item in data_map.json()["items"]}
    assert data_map_categories >= {
        "account_contact",
        "onboarding_consents",
        "voice_raw_audio",
        "blockchain_audit",
    }

    assert consents.status_code == 200
    consent_items = {item["key"]: item for item in consents.json()["items"]}
    assert consent_items["pdn_processing"]["status"] == "granted"
    assert consent_items["pdn_processing"]["consent_version"] == "pdn-v1"
    assert consent_items["marketing"]["required"] is False
    assert consent_items["marketing"]["purpose"] == "optional_notifications"


def test_issue_87_erasure_deletes_member_projection_and_is_journaled() -> None:
    repository = InMemoryWebCabinetRepository()
    _seed_privacy_projection(repository=repository, tenant_id="tenant-a")
    _seed_privacy_projection(
        repository=repository,
        tenant_id="tenant-b",
        content_title="tenant-b confidential content",
    )
    app = _app(repository=repository)
    client = TestClient(app)

    response = client.post(
        "/privacy/data-subject-requests",
        headers=_headers(subject="member-a", roles=("member_full",)),
        json={
            "request_id": "dsr-issue-87-erasure",
            "request_type": "erasure",
            "reason": "Отзыв согласия и удаление данных профиля",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["member_id"] == "member-a"
    assert body["request_type"] == "erasure"
    assert body["status"] == "completed"
    assert body["deleted_resources"] == {
        "cabinet_content": 1,
        "cabinet_contributions": 1,
        "onboarding_consents": 3,
        "onboarding_profile": 1,
        "onboarding_steps": 1,
    }
    assert body["retained_resources"] == [
        "audit_log_hashes",
        "legal_retention_records",
    ]
    assert len(body["audit_hash"]) == 64

    context = TenantContext(tenant_id="tenant-a", subject="member-a")
    assert (
        repository.get_onboarding_profile(
            context=context,
            member_id="member-a",
        )
        is None
    )
    assert (
        repository.list_onboarding_steps(
            context=context,
            member_id="member-a",
        )
        == ()
    )
    assert (
        repository.list_onboarding_consents(
            context=context,
            member_id="member-a",
        )
        == ()
    )
    assert (
        repository.list_content(
            context=context,
            owner_id="member-a",
        )
        == ()
    )

    tenant_b_context = TenantContext(tenant_id="tenant-b", subject="member-a")
    assert (
        repository.list_content(
            context=tenant_b_context,
            owner_id="member-a",
        )[0].title
        == "tenant-b confidential content"
    )

    state = app.state.web_cabinet_api
    assert isinstance(state, WebCabinetAPIState)
    assert (
        repository.get_data_subject_request(
            context=context,
            request_id="dsr-issue-87-erasure",
        )
        is not None
    )

    request_read = client.get(
        "/privacy/data-subject-requests/dsr-issue-87-erasure",
        headers=_headers(subject="member-a", roles=("member_full",)),
    )
    assert request_read.status_code == 200
    assert request_read.json()["audit_hash"] == body["audit_hash"]


def test_issue_87_processing_restriction_withdraws_optional_consents() -> None:
    repository = InMemoryWebCabinetRepository()
    _seed_privacy_projection(repository=repository, tenant_id="tenant-a")
    client = TestClient(_app(repository=repository))

    response = client.post(
        "/privacy/data-subject-requests",
        headers=_headers(subject="member-a", roles=("member_full",)),
        json={
            "request_id": "dsr-issue-87-restriction",
            "request_type": "processing_restriction",
            "reason": "Оставить только обязательную обработку",
            "consent_keys": [
                "pdn_processing",
                "marketing",
                "voice_processing",
            ],
        },
    )
    consents = client.get(
        "/privacy/consents",
        headers=_headers(subject="member-a", roles=("member_full",)),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["revoked_consents"] == ["marketing", "voice_processing"]
    consent_items = {item["key"]: item for item in consents.json()["items"]}
    assert consent_items["pdn_processing"]["status"] == "granted"
    assert consent_items["marketing"]["status"] == "withdrawn"
    assert consent_items["voice_processing"]["status"] == "withdrawn"
    assert consent_items["marketing"]["revoked_at"] is not None


def test_issue_87_compliance_docs_are_marked_implemented() -> None:
    compliance = (ROOT / "docs/COMPLIANCE.md").read_text(encoding="utf-8")
    web_cabinet = (ROOT / "docs/modules/web-cabinet.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/web-cabinet/README.md").read_text(encoding="utf-8")

    for marker in (
        "Статус аудита #87: пройден",
        "DSAR workflow",
        "POST /privacy/data-subject-requests",
        "GET /compliance/fz152/checklist",
    ):
        assert marker in compliance

    for marker in (
        "реализовано для #67, #68, #69, #70, #72, #73, #74 и #87",
        "GET** `/privacy/data-map`",
        "POST** `/privacy/data-subject-requests`",
    ):
        assert marker in web_cabinet

    for marker in (
        "ФЗ-152",
        "GET /privacy/consents",
        "POST /privacy/data-subject-requests",
    ):
        assert marker in readme


def _app(*, repository: InMemoryWebCabinetRepository) -> FastAPI:
    return create_web_cabinet_app(
        ServiceTemplateConfig(
            service_name="web-cabinet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        repository=repository,
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str = "corr-compliance-issue-87",
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


def _seed_privacy_projection(
    *,
    repository: InMemoryWebCabinetRepository,
    tenant_id: str,
    content_title: str = "Материал участника",
) -> None:
    repository.save_contribution(
        CabinetContributionRecord(
            tenant_id=tenant_id,
            member_id="member-a",
            period="2026-06",
            total_points=12.0,
            avg_points_council=100.0,
            kv_raw=0.02,
            kv_capped=0.02,
            payout_share=0.1,
            contribution_count=1,
        )
    )
    repository.add_content(
        CabinetContentRecord(
            tenant_id=tenant_id,
            owner_id="member-a",
            content_id="content-member-a",
            template_id="digest",
            title=content_title,
            preview="Публичный preview без ПДн.",
            content_hash="a" * 64,
            platform_targets=("telegram",),
            referral_links=(
                CabinetReferralLink(
                    level="L1",
                    owner_id="member-a",
                    url="https://authors.example/member-a",
                    reward_share=0.05,
                ),
            ),
            points_awarded=12.0,
            created_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC),
        )
    )
    repository.save_onboarding_profile(
        OnboardingProfileRecord(
            tenant_id=tenant_id,
            member_id="member-a",
            started_at=datetime(2026, 6, 20, 8, 0, tzinfo=UTC),
            target_window_hours=24,
            status_recommendation="member_full",
        )
    )
    repository.save_onboarding_step(
        OnboardingStepRecord(
            tenant_id=tenant_id,
            member_id="member-a",
            step_id="consents",
            title="Согласия и правила",
            description="Подтвердить обработку данных.",
            order=1,
            required=True,
            status="completed",
            completed_at=datetime(2026, 6, 20, 8, 30, tzinfo=UTC),
        )
    )
    for consent in (
        OnboardingConsentRecord(
            tenant_id=tenant_id,
            member_id="member-a",
            key="pdn_processing",
            label="Согласие на обработку ПДн",
            required=True,
            granted=True,
            granted_at=datetime(2026, 6, 20, 8, 35, tzinfo=UTC),
            consent_version="pdn-v1",
            purpose="account_and_participation",
            legal_basis="consent",
            retention_policy="until_contract_end_or_legal_retention",
            allowed_actions=("account", "onboarding", "support"),
        ),
        OnboardingConsentRecord(
            tenant_id=tenant_id,
            member_id="member-a",
            key="marketing",
            label="Согласие на уведомления и маркетинг",
            required=False,
            granted=True,
            granted_at=datetime(2026, 6, 20, 8, 36, tzinfo=UTC),
            consent_version="marketing-v1",
            purpose="optional_notifications",
            legal_basis="consent",
            retention_policy="until_withdrawal",
            allowed_actions=("notifications", "marketing"),
        ),
        OnboardingConsentRecord(
            tenant_id=tenant_id,
            member_id="member-a",
            key="voice_processing",
            label="Согласие на обработку голоса",
            required=False,
            granted=True,
            granted_at=datetime(2026, 6, 20, 8, 37, tzinfo=UTC),
            consent_version="voice-v1",
            purpose="voice_transcription",
            legal_basis="consent",
            retention_policy="raw_audio_24h_then_hash_only",
            allowed_actions=("voice_transcription",),
        ),
    ):
        repository.save_onboarding_consent(consent)
