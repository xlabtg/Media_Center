from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from web_cabinet import (
    InMemoryWebCabinetRepository,
    OnboardingAssistantAnswerRecord,
    OnboardingConsentRecord,
    OnboardingProfileRecord,
    OnboardingStepRecord,
    WebCabinetAPIState,
    create_web_cabinet_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "onboarding-issue-70-secret"
STARTED_AT = datetime(2026, 6, 19, 9, 0, tzinfo=UTC)


def test_issue_70_onboarding_tracks_progress_assistant_and_readiness() -> None:
    repository = InMemoryWebCabinetRepository()
    _seed_onboarding(
        repository=repository,
        tenant_id="tenant-a",
        member_id="candidate-a",
    )
    _seed_onboarding(
        repository=repository,
        tenant_id="tenant-b",
        member_id="candidate-a",
        answer="tenant-b confidential answer 999999",
    )
    client = TestClient(_app(repository=repository))

    overview = client.get(
        "/onboarding/overview",
        headers=_headers(subject="candidate-a", roles=("member_assoc",)),
    )
    html = client.get(
        "/onboarding",
        headers=_headers(subject="candidate-a", roles=("member_assoc",)),
    )
    answer = client.post(
        "/onboarding/assistant/answer",
        headers=_headers(subject="candidate-a", roles=("member_assoc",)),
        json={"question": "Как учитывается вклад?"},
    )

    assert overview.status_code == 200
    body = overview.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["member_id"] == "candidate-a"
    assert body["target_window_hours"] == 24
    assert body["started_at"] == "2026-06-19T09:00:00Z"
    assert body["target_finish_at"] == "2026-06-20T09:00:00Z"
    assert body["progress_percent"] == 100
    assert body["readiness"] == {
        "required_steps_total": 3,
        "completed_required_steps": 3,
        "required_consents_total": 2,
        "granted_required_consents": 2,
        "ready_for_review": True,
        "status": "ready_for_review",
        "blockers": [],
        "recommendation": "Передать анкету в Совет для проверки статуса",
    }
    assert [step["step_id"] for step in body["steps"]] == [
        "profile",
        "consents",
        "channels",
        "first_task",
    ]
    assert body["steps"][0]["status"] == "completed"
    assert body["steps"][3]["required"] is False
    assert [consent["key"] for consent in body["consents"]] == [
        "pdn_processing",
        "content_rules",
    ]
    assert body["assistant"]["enabled"] is True
    assert body["assistant"]["answered_questions"] == 2
    assert body["assistant"]["suggested_questions"][0]["question"] == (
        "Как учитывается вклад?"
    )
    assert "999999" not in overview.text

    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert '<meta name="viewport"' in html.text
    assert "@media (max-width: 760px)" in html.text
    assert "Онбординг участника" in html.text
    assert "100%" in html.text
    assert "AI-ассистент" in html.text
    assert "Готов к проверке" in html.text
    assert "Как учитывается вклад?" in html.text
    assert "999999" not in html.text

    assert answer.status_code == 200
    answer_body = answer.json()
    assert answer_body["matched_question_id"] == "faq-contribution"
    assert answer_body["answer"] == (
        "Вклад считается по событиям: идея, материал, публикация, усиление "
        "и модерация дают баллы, из которых формируется Кв."
    )
    assert answer_body["confidence"] == 0.92
    assert answer_body["escalation_available"] is True
    assert answer_body["source_refs"] == ["docs/VISION.md#6", "docs/ECONOMICS.md"]
    assert "999999" not in answer.text


def test_issue_70_onboarding_enforces_rbac_and_tenant_context() -> None:
    repository = InMemoryWebCabinetRepository()
    _seed_onboarding(
        repository=repository,
        tenant_id="tenant-a",
        member_id="candidate-a",
    )
    app = _app(repository=repository)
    client = TestClient(app)

    forbidden = client.get(
        "/onboarding/overview",
        headers=_headers(subject="candidate-b", roles=("member_assoc",)),
        params={"member_id": "candidate-a"},
    )
    council_read = client.get(
        "/onboarding/overview",
        headers=_headers(subject="council-1", roles=("council",)),
        params={"member_id": "candidate-a"},
    )
    headers = _headers(subject="candidate-a", roles=("member_assoc",))
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.get("/onboarding/overview", headers=headers)
    audience_self = client.get(
        "/onboarding/overview",
        headers=_headers(subject="candidate-a", roles=("audience",)),
    )

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"
    assert council_read.status_code == 200
    assert council_read.json()["member_id"] == "candidate-a"
    assert tenant_override.status_code == 403
    assert tenant_override.json()["error"]["code"] == "tenant_isolation_violation"
    assert audience_self.status_code == 200

    state = app.state.web_cabinet_api
    assert isinstance(state, WebCabinetAPIState)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"


def test_issue_70_onboarding_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/web-cabinet.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/web-cabinet/README.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано для #67, #68, #69 и #70",
        "GET** `/onboarding/overview`",
        "GET** `/onboarding`",
        "POST** `/onboarding/assistant/answer`",
        "tenant-isolation контракт #70",
        "проверка готовности участника",
    ):
        assert marker in spec

    for marker in (
        "Онбординг",
        "GET /onboarding/overview",
        "POST /onboarding/assistant/answer",
        "OnboardingProfileRecord",
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
    correlation_id: str = "corr-onboarding-issue-70",
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


def _seed_onboarding(
    *,
    repository: InMemoryWebCabinetRepository,
    tenant_id: str,
    member_id: str,
    answer: str = (
        "Вклад считается по событиям: идея, материал, публикация, усиление "
        "и модерация дают баллы, из которых формируется Кв."
    ),
) -> None:
    repository.save_onboarding_profile(
        OnboardingProfileRecord(
            tenant_id=tenant_id,
            member_id=member_id,
            started_at=STARTED_AT,
            target_window_hours=24,
            status_recommendation="member_assoc",
        )
    )
    for step in (
        OnboardingStepRecord(
            tenant_id=tenant_id,
            member_id=member_id,
            step_id="profile",
            title="Анкета участника",
            description="Заполнить базовую анкету без публикации ПДн.",
            order=1,
            required=True,
            status="completed",
            completed_at=datetime(2026, 6, 19, 10, 0, tzinfo=UTC),
        ),
        OnboardingStepRecord(
            tenant_id=tenant_id,
            member_id=member_id,
            step_id="consents",
            title="Согласия и правила",
            description="Подтвердить обработку данных и правила контента.",
            order=2,
            required=True,
            status="completed",
            completed_at=datetime(2026, 6, 19, 11, 0, tzinfo=UTC),
        ),
        OnboardingStepRecord(
            tenant_id=tenant_id,
            member_id=member_id,
            step_id="channels",
            title="Каналы связи",
            description="Подключить Telegram или email для уведомлений.",
            order=3,
            required=True,
            status="completed",
            completed_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
        ),
        OnboardingStepRecord(
            tenant_id=tenant_id,
            member_id=member_id,
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
            tenant_id=tenant_id,
            member_id=member_id,
            key="pdn_processing",
            label="Согласие на обработку данных",
            required=True,
            granted=True,
            granted_at=datetime(2026, 6, 19, 10, 30, tzinfo=UTC),
        ),
        OnboardingConsentRecord(
            tenant_id=tenant_id,
            member_id=member_id,
            key="content_rules",
            label="Правила достоверного контента",
            required=True,
            granted=True,
            granted_at=datetime(2026, 6, 19, 10, 35, tzinfo=UTC),
        ),
    ):
        repository.save_onboarding_consent(consent)

    for faq in (
        OnboardingAssistantAnswerRecord(
            tenant_id=tenant_id,
            question_id="faq-contribution",
            question="Как учитывается вклад?",
            answer=answer,
            confidence=0.92,
            source_refs=("docs/VISION.md#6", "docs/ECONOMICS.md"),
            topic_tags=("points", "kv"),
            escalation_available=True,
        ),
        OnboardingAssistantAnswerRecord(
            tenant_id=tenant_id,
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
        repository.save_onboarding_assistant_answer(faq)
