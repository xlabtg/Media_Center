from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

VOICE_TO_CHAIN_SERVICE_NAME = "voice-to-chain"
DEFAULT_RAW_AUDIO_TTL_HOURS = 24
MAX_RAW_AUDIO_TTL_HOURS = 24


@dataclass(frozen=True, slots=True)
class VoiceToChainSettings:
    raw_audio_ttl_hours: int = DEFAULT_RAW_AUDIO_TTL_HOURS
    whisper_cpp_binary_path: str = "whisper-cli"
    whisper_cpp_model_path: str | None = None
    whisper_cpp_language: str | None = None
    whisper_cpp_timeout_seconds: int = 300

    def __post_init__(self) -> None:
        if self.raw_audio_ttl_hours <= 0:
            raise ValueError("VOICE_RAW_AUDIO_TTL_HOURS должен быть положительным")
        if self.raw_audio_ttl_hours > MAX_RAW_AUDIO_TTL_HOURS:
            raise ValueError("сырой голос должен удаляться не позднее 24 часов")
        if self.whisper_cpp_binary_path.strip() == "":
            raise ValueError("WHISPER_CPP_BINARY_PATH должен быть непустой строкой")
        if self.whisper_cpp_timeout_seconds <= 0:
            raise ValueError("WHISPER_CPP_TIMEOUT_SECONDS должен быть положительным")

    @property
    def raw_audio_ttl(self) -> timedelta:
        return timedelta(hours=self.raw_audio_ttl_hours)


def build_voice_to_chain_settings(
    environ: Mapping[str, str] | None = None,
) -> VoiceToChainSettings:
    values = os.environ if environ is None else environ
    return VoiceToChainSettings(
        raw_audio_ttl_hours=_int_env(
            values,
            "VOICE_RAW_AUDIO_TTL_HOURS",
            default=DEFAULT_RAW_AUDIO_TTL_HOURS,
        ),
        whisper_cpp_binary_path=_env(
            values,
            "WHISPER_CPP_BINARY_PATH",
            default="whisper-cli",
        ),
        whisper_cpp_model_path=_optional_env(values, "WHISPER_CPP_MODEL_PATH"),
        whisper_cpp_language=_optional_env(values, "WHISPER_CPP_LANGUAGE"),
        whisper_cpp_timeout_seconds=_int_env(
            values,
            "WHISPER_CPP_TIMEOUT_SECONDS",
            default=300,
        ),
    )


def _env(values: Mapping[str, str], name: str, *, default: str) -> str:
    value = values.get(name)
    if value is None or value.strip() == "":
        return default

    return value.strip()


def _optional_env(values: Mapping[str, str], name: str) -> str | None:
    value = values.get(name)
    if value is None or value.strip() == "":
        return None

    return value.strip()


def _int_env(values: Mapping[str, str], name: str, *, default: int) -> int:
    value = values.get(name)
    if value is None or value.strip() == "":
        return default

    return int(value.strip())
