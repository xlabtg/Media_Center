from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from web_cabinet import (
    InMemoryWebCabinetRepository,
    OnboardingAssistantAnswerRecord,
    OnboardingConsentRecord,
    OnboardingProfileRecord,
    OnboardingStepRecord,
    create_web_cabinet_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "onboarding-demo-secret"
TENANT_ID = "tenant-a"
MEMBER_ID = "candidate-a"
STARTED_AT = datetime(2026, 6, 19, 9, 0, tzinfo=UTC)
DEMO_TOKEN = encode_hs256_jwt(
    {
        "tenant_id": TENANT_ID,
        "sub": MEMBER_ID,
        "roles": ["member_assoc"],
    },
    JWT_SECRET,
)


def build_demo_app() -> FastAPI:
    repository = InMemoryWebCabinetRepository()
    _seed_demo_data(repository=repository)
    return create_web_cabinet_app(
        ServiceTemplateConfig(
            service_name="web-cabinet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        repository=repository,
    )


def demo_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {DEMO_TOKEN}",
        "X-Tenant-Id": TENANT_ID,
        "X-Correlation-Id": "corr-onboarding-demo",
    }


def _seed_demo_data(*, repository: InMemoryWebCabinetRepository) -> None:
    repository.save_onboarding_profile(
        OnboardingProfileRecord(
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            started_at=STARTED_AT,
            target_window_hours=24,
            status_recommendation="member_assoc",
        )
    )
    for step in (
        OnboardingStepRecord(
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            step_id="profile",
            title="Анкета участника",
            description="Заполнить базовую анкету без публикации ПДн.",
            order=1,
            required=True,
            status="completed",
            completed_at=datetime(2026, 6, 19, 10, 0, tzinfo=UTC),
        ),
        OnboardingStepRecord(
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            step_id="consents",
            title="Согласия и правила",
            description="Подтвердить обработку данных и правила контента.",
            order=2,
            required=True,
            status="completed",
            completed_at=datetime(2026, 6, 19, 11, 0, tzinfo=UTC),
        ),
        OnboardingStepRecord(
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            step_id="channels",
            title="Каналы связи",
            description="Подключить Telegram или email для уведомлений.",
            order=3,
            required=True,
            status="completed",
            completed_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
        ),
        OnboardingStepRecord(
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            step_id="first_task",
            title="Первое действие",
            description="Выбрать первую задачу или предложить тему.",
            order=4,
            required=False,
            status="available",
            completed_at=None,
        ),
    ):
        repository.save_onboarding_step(step)

    for consent in (
        OnboardingConsentRecord(
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            key="pdn_processing",
            label="Согласие на обработку данных",
            required=True,
            granted=True,
            granted_at=datetime(2026, 6, 19, 10, 30, tzinfo=UTC),
        ),
        OnboardingConsentRecord(
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            key="content_rules",
            label="Правила достоверного контента",
            required=True,
            granted=True,
            granted_at=datetime(2026, 6, 19, 10, 35, tzinfo=UTC),
        ),
    ):
        repository.save_onboarding_consent(consent)

    for answer in (
        OnboardingAssistantAnswerRecord(
            tenant_id=TENANT_ID,
            question_id="faq-contribution",
            question="Как учитывается вклад?",
            answer=(
                "Вклад считается по событиям: идея, материал, публикация, "
                "усиление и модерация дают баллы, из которых формируется Кв."
            ),
            confidence=0.92,
            source_refs=("docs/VISION.md#6", "docs/ECONOMICS.md"),
            topic_tags=("points", "kv"),
            escalation_available=True,
        ),
        OnboardingAssistantAnswerRecord(
            tenant_id=TENANT_ID,
            question_id="faq-status",
            question="Когда Совет проверит мой статус?",
            answer=(
                "После обязательных шагов анкета попадает в ручную проверку; "
                "решение о статусе не принимается полностью автоматически."
            ),
            confidence=0.89,
            source_refs=("docs/REQUIREMENTS.md#FR-02",),
            topic_tags=("status", "council"),
            escalation_available=True,
        ),
    ):
        repository.save_onboarding_assistant_answer(answer)


app = build_demo_app()
