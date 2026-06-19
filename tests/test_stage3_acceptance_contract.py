from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from activity_command_center import (
    ActivityCommandCenterAPIState,
    create_activity_command_center_app,
)
from analytics_engine import (
    AnalyticsEngineAPIState,
    create_analytics_engine_app,
)
from blockchain_auditor import InMemoryGrpcBlockchainAuditTransport
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neuro_agent_orchestrator import (
    NeuroAgentOrchestratorAPIState,
    create_neuro_agent_orchestrator_app,
)
from notification_gateway import (
    NotificationGatewayAPIState,
    create_notification_gateway_app,
)
from policy_manager import PolicyManagerAPIState, create_policy_manager_app
from voice_to_chain import (
    InMemoryWhisperCppTranscriber,
    VoiceToChainAPIState,
    create_voice_to_chain_app,
)
from wallet import WalletAPIState, create_wallet_app

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "tenant-a"

POLICY_SECRET = "stage3-policy-secret"
ACTIVITY_SECRET = "stage3-activity-secret"
NEURO_SECRET = "stage3-neuro-agent-secret"
VOICE_SECRET = "stage3-voice-secret"
WALLET_SECRET = "stage3-wallet-secret"
ANALYTICS_SECRET = "stage3-analytics-secret"
NOTIFICATION_SECRET = "stage3-notification-secret"
PERIOD = "2026-W25"


def test_issue_66_stage3_extended_modules_acceptance_flow() -> None:
    policy_app = _policy_app()
    policy_client = TestClient(policy_app)
    policy_update = policy_client.put(
        "/policies/automation.max_autonomous_risk_score",
        headers=_headers(
            jwt_secret=POLICY_SECRET,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage3-policy-update",
        ),
        json={
            "value": {
                "kind": "threshold",
                "target": "risk_score",
                "operator": "lte",
                "threshold": 0.4,
                "reason": "risk_score_above_stage3_threshold",
                "decision_on_violation": "escalate",
            },
            "updated_at": "2026-06-19T09:00:00Z",
            "metadata": {"stage": "3", "issue": "66"},
        },
    )
    policy_decision = policy_client.post(
        "/policies/apply",
        headers=_headers(
            jwt_secret=POLICY_SECRET,
            subject="agent-onboarding",
            roles=("member_full",),
            correlation_id="corr-stage3-policy-apply",
        ),
        json={
            "policy_keys": ["automation.max_autonomous_risk_score"],
            "facts": {"risk_score": 0.55},
            "applied_at": "2026-06-19T09:05:00Z",
        },
    )

    assert policy_update.status_code == 200
    assert policy_update.json()["version"] == 2
    assert policy_decision.status_code == 200
    assert policy_decision.json()["decision"] == "escalate"
    assert policy_decision.json()["policy_versions"] == {
        "automation.max_autonomous_risk_score": 2,
    }

    activity_app = _activity_app()
    activity_client = TestClient(activity_app)
    thresholds = activity_client.put(
        "/thresholds",
        headers=_headers(
            jwt_secret=ACTIVITY_SECRET,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage3-activity-thresholds",
        ),
        json={
            "max_autonomous_risk_score": 0.4,
            "min_agent_confidence": 0.8,
            "operational_queue_limit": 25,
            "strategic_queue_limit": 10,
            "adaptive_queue_limit": 3,
            "updated_at": "2026-06-19T09:00:00Z",
            "metadata": {"source_policy_version": 2, "stage": "3"},
        },
    )
    onboarding_task = activity_client.post(
        "/tasks",
        headers=_headers(
            jwt_secret=ACTIVITY_SECRET,
            subject="agent-onboarding",
            roles=("member_full",),
            correlation_id="corr-stage3-onboarding-task",
        ),
        json={
            "task_id": "stage3-onboarding-review",
            "event_id": "evt-stage3-onboarding-review",
            "task_type": "ai_onboarding_review",
            "title": "Проверить AI-онбординг кандидата",
            "payload": {
                "candidate_hash": "sha256:" + "a" * 64,
                "target_window_hours": 36,
            },
            "assignee": "council-queue",
            "agent_id": "agent-onboarding",
            "risk_score": 0.55,
            "agent_confidence": 0.7,
            "feedback_loop": "operational",
            "created_at": "2026-06-19T09:10:00Z",
        },
    )
    activity_overview = activity_client.get(
        "/activity/overview",
        headers=_headers(
            jwt_secret=ACTIVITY_SECRET,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage3-activity-overview",
        ),
    )

    assert thresholds.status_code == 200
    assert thresholds.json()["revision"] == 2
    assert onboarding_task.status_code == 201
    assert onboarding_task.json()["status"] == "needs_council_review"
    assert onboarding_task.json()["policy_decision"] == "escalate"
    assert onboarding_task.json()["due_at"] == "2026-06-19T17:10:00Z"
    assert activity_overview.status_code == 200
    assert activity_overview.json()["queue_total"] == 1

    neuro_app = _neuro_app()
    neuro_client = TestClient(neuro_app)
    neuro_thresholds = neuro_client.put(
        "/thresholds",
        headers=_headers(
            jwt_secret=NEURO_SECRET,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage3-neuro-thresholds",
        ),
        json={
            "max_autonomous_risk_score": 0.4,
            "min_agent_confidence": 0.8,
            "max_autonomous_recipients": 3,
            "min_content_quality_score": 0.72,
            "allowed_template_keys": ["welcome", "faq_basic"],
            "metadata": {"source_policy_version": 2, "stage": "3"},
        },
    )
    auto_reply = neuro_client.post(
        "/agents/run",
        headers=_headers(
            jwt_secret=NEURO_SECRET,
            subject="agent-onboarding",
            roles=("member_full",),
            correlation_id="corr-stage3-neuro-reply",
        ),
        json={
            "run_id": "run-stage3-onboarding-reply",
            "event_id": "evt-stage3-onboarding-reply",
            "task_type": "engagement_auto_reply",
            "auto_reply": {
                "trigger_id": "onboarding-question-stage3",
                "platform": "telegram",
                "recipient_ref": "@raw-stage3-candidate",
                "template_key": "welcome",
                "risk_score": 0.2,
                "agent_confidence": 0.93,
                "estimated_recipients": 1,
                "created_at": "2026-06-19T09:15:00Z",
                "context": {"topic": "onboarding"},
            },
        },
    )

    assert neuro_thresholds.status_code == 200
    assert neuro_thresholds.json()["revision"] == 2
    assert auto_reply.status_code == 201
    assert auto_reply.json()["status"] == "completed"
    assert auto_reply.json()["policy_decision"] == "allow"
    assert auto_reply.json()["auto_reply"]["status"] == "sent"
    assert auto_reply.json()["decision_explanation"]["policy_decision"] == "allow"
    assert "@raw-stage3-candidate" not in auto_reply.text

    voice_transport = InMemoryGrpcBlockchainAuditTransport()
    voice_app = _voice_app(transport=voice_transport)
    voice_client = TestClient(voice_app)
    audio_bytes = b"RIFF\x00\x00\x00\x00WAVEstage3-onboarding-audio"
    voice_receipt = voice_client.post(
        "/voice/transcribe",
        headers=_headers(
            jwt_secret=VOICE_SECRET,
            subject="member-onboarding",
            roles=("member_full",),
            correlation_id="corr-stage3-voice",
        ),
        json={
            "audio_id": "audio-stage3-onboarding",
            "event_id": "evt-stage3-voice",
            "content_type": "audio/wav",
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "language": "ru",
            "captured_at": "2026-06-19T09:20:00Z",
        },
    )
    voice_cleanup = voice_client.post(
        "/voice/retention/cleanup",
        headers=_headers(
            jwt_secret=VOICE_SECRET,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage3-voice-cleanup",
        ),
        json={"now": "2026-06-20T09:20:01Z"},
    )

    assert voice_receipt.status_code == 201
    assert voice_receipt.json()["raw_audio_status"] == "pending_deletion"
    assert voice_receipt.json()["raw_audio_expires_at"] == "2026-06-20T09:20:00Z"
    assert len(voice_receipt.json()["transcript_sha256"]) == 64
    assert len(voice_transport.record_requests) == 1
    chain_metadata = voice_transport.record_requests[0].metadata
    assert (
        chain_metadata["transcript_sha256"] == voice_receipt.json()["transcript_sha256"]
    )
    assert "transcript" not in chain_metadata
    assert "voice" not in chain_metadata
    assert voice_cleanup.status_code == 200
    assert voice_cleanup.json()["deleted_count"] == 1

    wallet_app = _wallet_app()
    wallet_client = TestClient(wallet_app)
    wallet_credit = wallet_client.post(
        "/wallet/operations",
        headers=_headers(
            jwt_secret=WALLET_SECRET,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage3-wallet-credit",
            idempotency_key="stage3-wallet-credit",
        ),
        json={
            "operation_id": "stage3-wallet-credit",
            "member_id": "member-onboarding",
            "amount_mcv": "5.00",
            "type": "manual_adjustment",
            "ref_type": "onboarding_task",
            "ref_id": "stage3-onboarding-review",
            "period": "2026-06",
            "created_at": "2026-06-19T09:30:00Z",
            "metadata": {"stage": "3", "reason": "onboarding_credit"},
        },
    )
    wallet_balance = wallet_client.get(
        "/wallet/balance",
        headers=_headers(
            jwt_secret=WALLET_SECRET,
            subject="member-onboarding",
            roles=("member_full",),
            correlation_id="corr-stage3-wallet-balance",
        ),
        params={"member_id": "member-onboarding"},
    )

    assert wallet_credit.status_code == 201
    assert wallet_credit.json()["balance_after_mcv"] == "5.00"
    assert wallet_balance.status_code == 200
    assert wallet_balance.json()["balance_mcv"] == "5.00"

    analytics_app = _analytics_app()
    analytics_client = TestClient(analytics_app)
    _seed_stage3_kpi_events(analytics_client)
    kpi = analytics_client.get(
        "/analytics/kpi",
        headers=_headers(
            jwt_secret=ANALYTICS_SECRET,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage3-analytics-kpi",
        ),
        params={"period": PERIOD},
    )

    assert kpi.status_code == 200
    kpi_body = kpi.json()
    assert kpi_body["summary"] == {
        "metrics_total": 8,
        "metrics_on_track": 8,
        "metrics_below_target": 0,
        "metrics_above_target": 0,
    }
    assert {item["key"]: item["value"] for item in kpi_body["metrics"]}[
        "active_members"
    ] == 18

    notification_app = _notification_app()
    notification_client = TestClient(notification_app)
    preferences = notification_client.put(
        "/notify/preferences",
        headers=_headers(
            jwt_secret=NOTIFICATION_SECRET,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage3-notify-preferences",
        ),
        json={
            "recipient_id": "council-queue",
            "channels": ["telegram", "email"],
            "event_types": ["stage3.onboarding_escalated"],
            "template_overrides": {
                "stage3.onboarding_escalated": "stage3_council_alert",
            },
        },
    )
    notification = notification_client.post(
        "/notify",
        headers=_headers(
            jwt_secret=NOTIFICATION_SECRET,
            subject="board-1",
            roles=("board",),
            correlation_id="corr-stage3-notify",
        ),
        json={
            "event_id": "evt-stage3-onboarding-escalated",
            "event_type": "stage3.onboarding_escalated",
            "source": "activity-command-center",
            "recipients": ["council-queue"],
            "channels": ["telegram", "email"],
            "template": {
                "template_key": "stage3_council_alert",
                "subject_template": "AI-онбординг {{ task_id }} ждёт решения",
                "body_template": (
                    "Tenant {{ tenant_id }}: задача {{ task_id }} "
                    "эскалирована по политике {{ policy_version }}"
                ),
                "channels": ["telegram", "email"],
            },
            "context": {
                "task_id": "stage3-onboarding-review",
                "policy_version": "2",
            },
            "priority": "urgent",
            "occurred_at": "2026-06-19T09:35:00Z",
            "metadata": {"stage": "3", "issue": "66"},
        },
    )

    assert preferences.status_code == 200
    assert notification.status_code == 202
    assert notification.json()["delivered_count"] == 2
    assert notification.json()["skipped_count"] == 0

    policy_state = cast(PolicyManagerAPIState, policy_app.state.policy_manager_api)
    activity_state = cast(
        ActivityCommandCenterAPIState,
        activity_app.state.activity_command_center_api,
    )
    neuro_state = cast(NeuroAgentOrchestratorAPIState, neuro_app.state.neuro_agent_api)
    voice_state = cast(VoiceToChainAPIState, voice_app.state.voice_to_chain_api)
    wallet_state = cast(WalletAPIState, wallet_app.state.wallet_api)
    analytics_state = cast(
        AnalyticsEngineAPIState,
        analytics_app.state.analytics_engine_api,
    )
    notification_state = cast(
        NotificationGatewayAPIState,
        notification_app.state.notification_gateway_api,
    )

    assert [record.event_type for record in policy_state.audit_log_sink.records] == [
        "policy.updated",
    ]
    assert [record.event_type for record in activity_state.audit_log_sink.records] == [
        "activity.thresholds.updated",
        "activity.task.created",
    ]
    assert [record.event_type for record in neuro_state.audit_log_sink.records] == [
        "neuro_agent.thresholds.updated",
        "neuro_agent.auto_reply.sent",
    ]
    assert (
        voice_state.service.audio_store.read_audio_bytes(
            tenant_id=TENANT_ID,
            audio_id="audio-stage3-onboarding",
        )
        is None
    )
    assert [record.event_type for record in wallet_state.audit_log_sink.records] == [
        "wallet.operation_recorded",
    ]
    assert len(analytics_state.audit_log_sink.records) == 25
    assert [delivery.channel for delivery in notification_state.channel.deliveries] == [
        "telegram",
        "email",
    ]

    wallet_event = wallet_state.publisher.messages[0].envelope.payload
    assert "member_id" not in wallet_event
    assert "amount_mcv" not in wallet_event

    neuro_event_json = "".join(
        message.envelope.to_json() for message in neuro_state.publisher.messages
    )
    notification_event_json = notification_state.publisher.messages[
        -1
    ].envelope.to_json()
    assert "@raw-stage3-candidate" not in neuro_event_json
    assert "AI-онбординг stage3-onboarding-review" not in notification_event_json


def test_issue_66_stage3_acceptance_snapshot_covers_epic_criteria() -> None:
    acceptance = _read_text("docs/STAGE_3_ACCEPTANCE.md")
    readme = _read_text("README.md")

    for marker in (
        "Статус: acceptance snapshot для issue #66",
        "## 1. Решение по этапу 3",
        "## 2. Трассировка задач #54, #58-#65",
        "## 3. Критерии завершения эпика #66",
        "Работает онбординг и базовая автоматизация под порогами Совета",
        "Голос превращается в хэш в блокчейне с авто-удалением исходника",
        "Считаются KPI и работают уведомления",
        "## 4. Gate перед этапом 4",
        "pytest tests/test_stage3_acceptance_contract.py",
    ):
        assert marker in acceptance

    for issue in (54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66):
        assert f"#{issue}" in acceptance

    for marker in (
        "services/activity-command-center/activity_command_center/api.py",
        "services/neuro-agent-orchestrator/neuro_agent_orchestrator/api.py",
        "services/voice-to-chain/voice_to_chain/api.py",
        "services/wallet/wallet/api.py",
        "services/analytics-engine/analytics_engine/api.py",
        "services/notification-gateway/notification_gateway/api.py",
        "services/policy-manager/policy_manager/api.py",
        "tests/test_stage3_acceptance_contract.py",
    ):
        assert marker in acceptance

    assert "docs/STAGE_3_ACCEPTANCE.md" in readme


def _policy_app() -> FastAPI:
    return create_policy_manager_app(_config("policy-manager", POLICY_SECRET))


def _activity_app() -> FastAPI:
    return create_activity_command_center_app(
        _config("activity-command-center", ACTIVITY_SECRET)
    )


def _neuro_app() -> FastAPI:
    return create_neuro_agent_orchestrator_app(
        _config("neuro-agent-orchestrator", NEURO_SECRET)
    )


def _voice_app(*, transport: InMemoryGrpcBlockchainAuditTransport) -> FastAPI:
    transcriber = InMemoryWhisperCppTranscriber(
        transcripts={
            "audio-stage3-onboarding": "Кандидат подтвердил участие в онбординге",
        },
        model_name="whisper.cpp-stage3-test-model",
        completed_at=datetime(2026, 6, 19, 9, 20, tzinfo=UTC),
    )
    return create_voice_to_chain_app(
        _config("voice-to-chain", VOICE_SECRET),
        transcriber=transcriber,
        blockchain_transport=transport,
        clock=lambda: datetime(2026, 6, 19, 9, 20, tzinfo=UTC),
    )


def _wallet_app() -> FastAPI:
    return create_wallet_app(_config("wallet", WALLET_SECRET))


def _analytics_app() -> FastAPI:
    return create_analytics_engine_app(_config("analytics-engine", ANALYTICS_SECRET))


def _notification_app() -> FastAPI:
    return create_notification_gateway_app(
        _config("notification-gateway", NOTIFICATION_SECRET)
    )


def _config(service_name: str, jwt_secret: str) -> ServiceTemplateConfig:
    return ServiceTemplateConfig(
        service_name=service_name,
        version="0.1.0",
        jwt_secret=jwt_secret,
        prometheus_enabled=True,
    )


def _seed_stage3_kpi_events(client: TestClient) -> None:
    for member_index in range(18):
        _record_analytics_event(
            client,
            {
                "event_id": f"evt-stage3-active-member-{member_index}",
                "type": "member_active",
                "period": PERIOD,
                "member_id": f"member-{member_index}",
                "occurred_at": "2026-06-19T10:00:00Z",
            },
            subject=f"member-{member_index}",
            roles=("member_full",),
            correlation_id=f"corr-stage3-active-member-{member_index}",
        )

    for payload in (
        {"event_id": "evt-stage3-new-members", "type": "member_joined", "value": 4},
        {
            "event_id": "evt-stage3-materials",
            "type": "material_published",
            "value": 24,
        },
        {"event_id": "evt-stage3-views", "type": "content_viewed", "value": 12_500},
        {
            "event_id": "evt-stage3-reading",
            "type": "reading_time_recorded",
            "value": 24_000,
            "sample_count": 100,
        },
        {"event_id": "evt-stage3-comments", "type": "comment_created", "value": 60},
        {"event_id": "evt-stage3-tasks", "type": "task_completed", "value": 14},
        {
            "event_id": "evt-stage3-initiatives",
            "type": "initiative_created",
            "value": 2,
        },
    ):
        _record_analytics_event(
            client,
            {
                **payload,
                "period": PERIOD,
                "occurred_at": "2026-06-19T10:30:00Z",
            },
            subject="board-1",
            roles=("board",),
            correlation_id=f"corr-{payload['event_id']}",
        )


def _record_analytics_event(
    client: TestClient,
    payload: dict[str, object],
    *,
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str,
) -> None:
    response = client.post(
        "/analytics/events",
        headers=_headers(
            jwt_secret=ANALYTICS_SECRET,
            subject=subject,
            roles=roles,
            correlation_id=correlation_id,
        ),
        json=payload,
    )
    assert response.status_code == 201


def _headers(
    *,
    jwt_secret: str,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str = TENANT_ID,
    correlation_id: str,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": list(roles),
        },
        jwt_secret,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": correlation_id,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key

    return headers


def _read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")
