from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from blockchain_auditor import (
    GrpcBlockchainAuditConnector,
    InMemoryGrpcBlockchainAuditTransport,
    build_blockchain_auditor_settings,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from voice_to_chain import InMemoryWhisperCppTranscriber, VoiceToChainService
from web_cabinet import WebCabinetAPIState, create_web_cabinet_app

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "voice-assistant-issue-72-secret"
CAPTURED_AT = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)


def test_issue_72_voice_assistant_records_transcribes_and_shows_status() -> None:
    transport = InMemoryGrpcBlockchainAuditTransport()
    transcriber = InMemoryWhisperCppTranscriber(
        transcripts={"audio-issue-72": "Голосовая заметка зафиксирована"},
        completed_at=datetime(2026, 6, 20, 10, 1, tzinfo=UTC),
    )
    app = _app(
        voice_to_chain_service=_voice_service(
            transcriber=transcriber,
            transport=transport,
        ),
    )
    client = TestClient(app)
    audio_bytes = b"RIFF\x00\x00\x00\x00WAVEissue-72-voice"

    html = client.get(
        "/voice-assistant",
        headers=_headers(subject="member-a", roles=("member_full",)),
    )
    receipt = client.post(
        "/voice-assistant/transcribe",
        headers=_headers(
            subject="member-a",
            roles=("member_full",),
            correlation_id="corr-voice-assistant-issue-72",
        ),
        json={
            "audio_id": "audio-issue-72",
            "event_id": "evt-voice-assistant-issue-72",
            "content_type": "audio/webm;codecs=opus",
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "language": "ru",
            "captured_at": CAPTURED_AT.isoformat().replace("+00:00", "Z"),
        },
    )

    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert '<meta name="viewport"' in html.text
    assert "MediaRecorder" in html.text
    assert "navigator.mediaDevices.getUserMedia" in html.text
    assert "/voice-assistant/transcribe" in html.text
    assert "voice.transcript.recorded" in html.text
    assert "transcript_sha256" in html.text
    assert "raw_audio_status" in html.text

    assert receipt.status_code == 201
    body = receipt.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["audio_id"] == "audio-issue-72"
    assert body["event_id"] == "evt-voice-assistant-issue-72"
    assert body["transcript"] == "Голосовая заметка зафиксирована"
    assert body["language"] == "ru"
    assert body["raw_audio_status"] == "pending_deletion"
    assert body["raw_audio_expires_at"] == "2026-06-21T10:00:00Z"
    assert len(body["transcript_sha256"]) == 64
    assert len(body["audio_sha256"]) == 64
    assert len(body["audit_hash"]) == 64
    assert body["correlation_id"] == "corr-voice-assistant-issue-72"
    assert transcriber.requests[0].content_type == "audio/webm;codecs=opus"
    assert transcriber.requests[0].metadata == {
        "captured_at": "2026-06-20T10:00:00Z",
    }
    assert len(transport.record_requests) == 1
    chain_metadata = transport.record_requests[0].metadata
    assert chain_metadata["transcript_sha256"] == body["transcript_sha256"]
    assert chain_metadata["audio_sha256"] == body["audio_sha256"]
    assert chain_metadata["raw_audio_expires_at"] == body["raw_audio_expires_at"]
    assert "transcript" not in chain_metadata
    assert "voice" not in chain_metadata


def test_issue_72_voice_assistant_enforces_rbac_and_tenant_context() -> None:
    app = _app()
    client = TestClient(app)

    forbidden = client.get(
        "/voice-assistant",
        headers=_headers(subject="audience-1", roles=("audience",)),
    )
    headers = _headers(subject="member-a", roles=("member_full",))
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.post(
        "/voice-assistant/transcribe",
        headers=headers,
        json={
            "audio_id": "audio-tenant-override",
            "event_id": "evt-tenant-override",
            "content_type": "audio/wav",
            "audio_base64": base64.b64encode(b"voice").decode("ascii"),
        },
    )

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"
    assert tenant_override.status_code == 403
    assert tenant_override.json()["error"]["code"] == "tenant_isolation_violation"
    state = cast(WebCabinetAPIState, app.state.web_cabinet_api)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"


def test_issue_72_voice_assistant_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/web-cabinet.md").read_text(encoding="utf-8")
    voice_spec = (ROOT / "docs/modules/voice-to-chain.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/web-cabinet/README.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано для #67, #68, #69, #70 и #72",
        "GET** `/voice-assistant`",
        "POST** `/voice-assistant/transcribe`",
        "MediaRecorder",
        "Voice-to-Chain receipt",
        "tenant-isolation контракт #72",
    ):
        assert marker in spec

    for marker in (
        "UI голосового ассистента",
        "GET /voice-assistant",
        "POST /voice-assistant/transcribe",
        "VoiceToChainService",
    ):
        assert marker in readme

    assert "UI голосового ассистента реализован в Web Cabinet для #72" in voice_spec


def _app(
    *,
    voice_to_chain_service: VoiceToChainService | None = None,
) -> FastAPI:
    return create_web_cabinet_app(
        ServiceTemplateConfig(
            service_name="web-cabinet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        voice_to_chain_service=voice_to_chain_service,
    )


def _voice_service(
    *,
    transcriber: InMemoryWhisperCppTranscriber,
    transport: InMemoryGrpcBlockchainAuditTransport,
) -> VoiceToChainService:
    return VoiceToChainService(
        transcriber=transcriber,
        blockchain_connector=GrpcBlockchainAuditConnector(
            settings=build_blockchain_auditor_settings(environ={}),
            transport=transport,
        ),
        clock=lambda: CAPTURED_AT,
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str = "corr-voice-assistant-issue-72",
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
