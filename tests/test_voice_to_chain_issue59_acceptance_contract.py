from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from blockchain_auditor import InMemoryGrpcBlockchainAuditTransport
from fastapi.testclient import TestClient
from voice_to_chain import (
    InMemoryWhisperCppTranscriber,
    VoiceToChainAPIState,
    create_voice_to_chain_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "voice-to-chain-issue-59-secret"


def test_issue_59_voice_to_chain_transcribes_audits_and_deletes_audio() -> None:
    transport = InMemoryGrpcBlockchainAuditTransport()
    transcriber = InMemoryWhisperCppTranscriber(
        transcripts={"audio-issue-59": "Проверяемая запись Совета"},
        model_name="whisper.cpp-test-model",
        completed_at=datetime(2026, 6, 19, 6, 0, tzinfo=UTC),
    )
    app = create_voice_to_chain_app(
        ServiceTemplateConfig(
            service_name="voice-to-chain",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        transcriber=transcriber,
        blockchain_transport=transport,
        clock=lambda: datetime(2026, 6, 19, 6, 0, tzinfo=UTC),
    )
    client = TestClient(app)
    headers = _headers()
    audio_bytes = b"RIFF\x00\x00\x00\x00WAVEvoice-audio"

    response = client.post(
        "/voice/transcribe",
        headers=headers,
        json={
            "audio_id": "audio-issue-59",
            "event_id": "evt-voice-issue-59",
            "content_type": "audio/wav",
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "language": "ru",
            "captured_at": "2026-06-19T05:59:00Z",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["audio_id"] == "audio-issue-59"
    assert body["event_id"] == "evt-voice-issue-59"
    assert body["transcript"] == "Проверяемая запись Совета"
    assert body["transcriber"] == "whisper.cpp"
    assert body["raw_audio_status"] == "pending_deletion"
    assert body["raw_audio_expires_at"] == "2026-06-20T06:00:00Z"
    assert len(body["transcript_sha256"]) == 64
    assert len(body["audit_hash"]) == 64
    assert body["block_ref"] == (
        "grpc://localhost:50051/audit/tenant-a/evt-voice-issue-59"
    )

    assert len(transport.record_requests) == 1
    chain_command = transport.record_requests[0]
    assert chain_command.event_type == "voice.transcript.recorded"
    assert chain_command.audit_hash == body["audit_hash"]
    assert chain_command.metadata["transcript_sha256"] == body["transcript_sha256"]
    assert chain_command.metadata["audio_sha256"] == body["audio_sha256"]
    assert "transcript" not in chain_command.metadata
    assert "voice" not in chain_command.metadata
    assert "text" not in chain_command.metadata
    assert transcriber.requests[0].metadata == {"captured_at": "2026-06-19T05:59:00Z"}

    state = cast(VoiceToChainAPIState, app.state.voice_to_chain_api)
    stored = state.service.audio_store.get_audio(
        tenant_id="tenant-a",
        audio_id="audio-issue-59",
    )
    assert stored is not None
    assert stored.status == "pending_deletion"
    assert (
        state.service.audio_store.read_audio_bytes(
            tenant_id="tenant-a",
            audio_id="audio-issue-59",
        )
        == audio_bytes
    )

    cleanup = client.post(
        "/voice/retention/cleanup",
        headers=_headers(subject="council-1", roles=("council",)),
        json={"now": "2026-06-20T06:00:01Z"},
    )

    assert cleanup.status_code == 200
    cleanup_body = cleanup.json()
    assert cleanup_body["deleted_count"] == 1
    assert cleanup_body["items"][0]["audio_id"] == "audio-issue-59"
    assert cleanup_body["items"][0]["deleted_at"] == "2026-06-20T06:00:01Z"
    assert (
        state.service.audio_store.read_audio_bytes(
            tenant_id="tenant-a",
            audio_id="audio-issue-59",
        )
        is None
    )
    deleted = state.service.audio_store.get_audio(
        tenant_id="tenant-a",
        audio_id="audio-issue-59",
    )
    assert deleted is not None
    assert deleted.status == "deleted"
    assert deleted.deleted_at == datetime(2026, 6, 20, 6, 0, 1, tzinfo=UTC)


def test_issue_59_voice_to_chain_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/voice-to-chain.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/voice-to-chain/README.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано",
        "Whisper.cpp",
        "voice.transcript.recorded",
        "transcript_sha256",
        "TTL-очистка",
    ):
        assert marker in spec

    for marker in (
        "create_voice_to_chain_app",
        "POST /voice/transcribe",
        "POST /voice/retention/cleanup",
        "InMemoryWhisperCppTranscriber",
    ):
        assert marker in readme


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str = "member-1",
    roles: tuple[str, ...] = ("member_full",),
    correlation_id: str = "corr-voice-issue-59",
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


def test_issue_59_cleanup_does_not_delete_audio_before_24h_ttl() -> None:
    transport = InMemoryGrpcBlockchainAuditTransport()
    transcriber = InMemoryWhisperCppTranscriber(
        transcripts={"audio-ttl": "Черновик голоса"},
        completed_at=datetime(2026, 6, 19, 6, 0, tzinfo=UTC),
    )
    app = create_voice_to_chain_app(
        ServiceTemplateConfig(
            service_name="voice-to-chain",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        transcriber=transcriber,
        blockchain_transport=transport,
        clock=lambda: datetime(2026, 6, 19, 6, 0, tzinfo=UTC),
    )
    client = TestClient(app)
    audio_bytes = b"voice-audio"

    created = client.post(
        "/voice/transcribe",
        headers=_headers(correlation_id="corr-voice-ttl"),
        json={
            "audio_id": "audio-ttl",
            "event_id": "evt-voice-ttl",
            "content_type": "audio/ogg",
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
        },
    )
    cleanup = client.post(
        "/voice/retention/cleanup",
        headers=_headers(
            subject="council-1",
            roles=("council",),
            correlation_id="corr-voice-cleanup-before-ttl",
        ),
        json={
            "now": (
                datetime(2026, 6, 19, 6, 0, tzinfo=UTC) + timedelta(hours=23)
            ).isoformat()
        },
    )

    assert created.status_code == 201
    assert cleanup.status_code == 200
    assert cleanup.json()["deleted_count"] == 0
    state = cast(VoiceToChainAPIState, app.state.voice_to_chain_api)
    assert (
        state.service.audio_store.read_audio_bytes(
            tenant_id="tenant-a",
            audio_id="audio-ttl",
        )
        == audio_bytes
    )
