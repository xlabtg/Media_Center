from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_to_chain.api import (
        VOICE_RETENTION_CLEANUP_POLICY,
        VOICE_TRANSCRIBE_POLICY,
        VoiceRetentionCleanupRequest,
        VoiceRetentionCleanupResponse,
        VoiceToChainAPIState,
        VoiceTranscriptionRequest,
        create_voice_to_chain_app,
    )
    from voice_to_chain.retention import (
        AudioDeletionReceipt,
        AudioRetentionError,
        AudioRetentionStatus,
        InMemoryTemporaryAudioStore,
        TemporaryAudioRecord,
        hash_bytes,
    )
    from voice_to_chain.service import (
        VOICE_AUDIO_DELETED_EVENT,
        VOICE_TO_CHAIN_SCHEMA_VERSION,
        VOICE_TO_CHAIN_SOURCE,
        VOICE_TRANSCRIPT_RECORDED_EVENT,
        VoiceToChainError,
        VoiceToChainService,
        VoiceTranscriptionCommand,
        VoiceTranscriptionReceipt,
    )
    from voice_to_chain.settings import (
        DEFAULT_RAW_AUDIO_TTL_HOURS,
        MAX_RAW_AUDIO_TTL_HOURS,
        VOICE_TO_CHAIN_SERVICE_NAME,
        VoiceToChainSettings,
        build_voice_to_chain_settings,
    )
    from voice_to_chain.transcription import (
        AudioTranscriptionInput,
        InMemoryWhisperCppTranscriber,
        WhisperCppCliConfig,
        WhisperCppCliTranscriber,
        WhisperCppTranscriber,
        WhisperCppTranscript,
        WhisperCppTranscriptionError,
    )

_EXPORTS: dict[str, str] = {
    "VOICE_RETENTION_CLEANUP_POLICY": "voice_to_chain.api",
    "VOICE_TRANSCRIBE_POLICY": "voice_to_chain.api",
    "VoiceRetentionCleanupRequest": "voice_to_chain.api",
    "VoiceRetentionCleanupResponse": "voice_to_chain.api",
    "VoiceToChainAPIState": "voice_to_chain.api",
    "VoiceTranscriptionRequest": "voice_to_chain.api",
    "create_voice_to_chain_app": "voice_to_chain.api",
    "AudioDeletionReceipt": "voice_to_chain.retention",
    "AudioRetentionError": "voice_to_chain.retention",
    "AudioRetentionStatus": "voice_to_chain.retention",
    "InMemoryTemporaryAudioStore": "voice_to_chain.retention",
    "TemporaryAudioRecord": "voice_to_chain.retention",
    "hash_bytes": "voice_to_chain.retention",
    "VOICE_AUDIO_DELETED_EVENT": "voice_to_chain.service",
    "VOICE_TO_CHAIN_SCHEMA_VERSION": "voice_to_chain.service",
    "VOICE_TO_CHAIN_SOURCE": "voice_to_chain.service",
    "VOICE_TRANSCRIPT_RECORDED_EVENT": "voice_to_chain.service",
    "VoiceToChainError": "voice_to_chain.service",
    "VoiceToChainService": "voice_to_chain.service",
    "VoiceTranscriptionCommand": "voice_to_chain.service",
    "VoiceTranscriptionReceipt": "voice_to_chain.service",
    "DEFAULT_RAW_AUDIO_TTL_HOURS": "voice_to_chain.settings",
    "MAX_RAW_AUDIO_TTL_HOURS": "voice_to_chain.settings",
    "VOICE_TO_CHAIN_SERVICE_NAME": "voice_to_chain.settings",
    "VoiceToChainSettings": "voice_to_chain.settings",
    "build_voice_to_chain_settings": "voice_to_chain.settings",
    "AudioTranscriptionInput": "voice_to_chain.transcription",
    "InMemoryWhisperCppTranscriber": "voice_to_chain.transcription",
    "WhisperCppCliConfig": "voice_to_chain.transcription",
    "WhisperCppCliTranscriber": "voice_to_chain.transcription",
    "WhisperCppTranscriber": "voice_to_chain.transcription",
    "WhisperCppTranscript": "voice_to_chain.transcription",
    "WhisperCppTranscriptionError": "voice_to_chain.transcription",
}

__all__ = [
    "DEFAULT_RAW_AUDIO_TTL_HOURS",
    "MAX_RAW_AUDIO_TTL_HOURS",
    "VOICE_AUDIO_DELETED_EVENT",
    "VOICE_RETENTION_CLEANUP_POLICY",
    "VOICE_TO_CHAIN_SCHEMA_VERSION",
    "VOICE_TO_CHAIN_SERVICE_NAME",
    "VOICE_TO_CHAIN_SOURCE",
    "VOICE_TRANSCRIBE_POLICY",
    "VOICE_TRANSCRIPT_RECORDED_EVENT",
    "AudioDeletionReceipt",
    "AudioRetentionError",
    "AudioRetentionStatus",
    "AudioTranscriptionInput",
    "InMemoryTemporaryAudioStore",
    "InMemoryWhisperCppTranscriber",
    "TemporaryAudioRecord",
    "VoiceRetentionCleanupRequest",
    "VoiceRetentionCleanupResponse",
    "VoiceToChainAPIState",
    "VoiceToChainError",
    "VoiceToChainService",
    "VoiceToChainSettings",
    "VoiceTranscriptionCommand",
    "VoiceTranscriptionReceipt",
    "VoiceTranscriptionRequest",
    "WhisperCppCliConfig",
    "WhisperCppCliTranscriber",
    "WhisperCppTranscript",
    "WhisperCppTranscriber",
    "WhisperCppTranscriptionError",
    "build_voice_to_chain_settings",
    "create_voice_to_chain_app",
    "hash_bytes",
]


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
