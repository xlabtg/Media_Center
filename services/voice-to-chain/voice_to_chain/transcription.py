from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import Field, field_validator

from libs.shared.models import (
    JSONValue,
    SharedBaseModel,
)

from .retention import normalize_datetime


class WhisperCppTranscriptionError(RuntimeError):
    """Raised when local Whisper.cpp transcription fails."""


@dataclass(frozen=True, slots=True)
class AudioTranscriptionInput:
    tenant_id: str
    audio_id: str
    content_type: str
    audio_sha256: str
    audio_bytes: bytes
    language: str | None = None
    metadata: Mapping[str, JSONValue] | None = None


class WhisperCppTranscript(SharedBaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    language: str | None = Field(default=None, min_length=2, max_length=16)
    transcriber: str = Field(default="whisper.cpp", pattern=r"^whisper\.cpp$")
    model_name: str = Field(min_length=1, max_length=256)
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def _normalize_completed_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class WhisperCppTranscriber(Protocol):
    async def transcribe(self, audio: AudioTranscriptionInput) -> WhisperCppTranscript:
        """Transcribe raw audio locally with Whisper.cpp-compatible semantics."""


@dataclass(slots=True)
class InMemoryWhisperCppTranscriber:
    """Deterministic Whisper.cpp-compatible transcriber for tests."""

    transcripts: Mapping[str, str] = field(default_factory=dict)
    default_transcript: str = "Тестовая локальная транскрипция"
    model_name: str = "whisper.cpp-inmemory"
    completed_at: datetime | None = None
    _requests: list[AudioTranscriptionInput] = field(
        default_factory=list,
        init=False,
    )

    @property
    def requests(self) -> tuple[AudioTranscriptionInput, ...]:
        return tuple(self._requests)

    async def transcribe(self, audio: AudioTranscriptionInput) -> WhisperCppTranscript:
        self._requests.append(audio)
        text = self.transcripts.get(audio.audio_id, self.default_transcript).strip()
        if text == "":
            raise WhisperCppTranscriptionError("Whisper.cpp вернул пустой transcript")

        return WhisperCppTranscript(
            text=text,
            language=audio.language,
            model_name=self.model_name,
            completed_at=self.completed_at or datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class WhisperCppCliConfig:
    binary_path: str = "whisper-cli"
    model_path: str | None = None
    default_language: str | None = None
    timeout_seconds: int = 300

    def __post_init__(self) -> None:
        if self.binary_path.strip() == "":
            raise ValueError("WHISPER_CPP_BINARY_PATH должен быть непустой строкой")
        if self.timeout_seconds <= 0:
            raise ValueError("WHISPER_CPP_TIMEOUT_SECONDS должен быть положительным")


@dataclass(frozen=True, slots=True)
class WhisperCppCliTranscriber:
    """Local Whisper.cpp CLI adapter.

    The adapter expects a whisper.cpp-compatible binary such as `whisper-cli`.
    Tests use `InMemoryWhisperCppTranscriber`, so CI does not need model files.
    """

    config: WhisperCppCliConfig = field(default_factory=WhisperCppCliConfig)

    async def transcribe(self, audio: AudioTranscriptionInput) -> WhisperCppTranscript:
        language = audio.language or self.config.default_language
        with tempfile.TemporaryDirectory(prefix="voice-to-chain-") as directory:
            workdir = Path(directory)
            audio_path = workdir / f"{audio.audio_id}.audio"
            output_prefix = workdir / "transcript"
            output_path = output_prefix.with_suffix(".txt")
            audio_path.write_bytes(audio.audio_bytes)

            args = [self.config.binary_path]
            if self.config.model_path is not None:
                args.extend(["-m", self.config.model_path])
            args.extend(["-f", str(audio_path), "-otxt", "-of", str(output_prefix)])
            if language is not None:
                args.extend(["-l", language])

            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.config.timeout_seconds,
                )
            except TimeoutError as error:
                process.kill()
                await process.communicate()
                raise WhisperCppTranscriptionError(
                    "Whisper.cpp transcription превысила timeout"
                ) from error

            if process.returncode != 0:
                raise WhisperCppTranscriptionError(
                    _decode_cli_error(stderr) or "Whisper.cpp завершился с ошибкой"
                )

            text = _read_transcript_text(output_path=output_path, stdout=stdout)
            if text == "":
                raise WhisperCppTranscriptionError(
                    "Whisper.cpp вернул пустой transcript"
                )

            return WhisperCppTranscript(
                text=text,
                language=language,
                model_name=self.config.model_path or self.config.binary_path,
                completed_at=datetime.now(UTC),
            )


def _read_transcript_text(*, output_path: Path, stdout: bytes) -> str:
    if output_path.exists():
        return output_path.read_text(encoding="utf-8").strip()

    return stdout.decode("utf-8", errors="replace").strip()


def _decode_cli_error(stderr: bytes) -> str:
    return stderr.decode("utf-8", errors="replace").strip()
