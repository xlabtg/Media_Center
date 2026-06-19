# Voice-to-Chain

Сервис принимает голосовой ввод, выполняет локальную транскрипцию через
Whisper.cpp-compatible adapter, фиксирует hash-only запись результата в Private
Blockchain Auditor и удаляет исходное аудио по TTL не позднее 24 часов.

## Интерфейсы

- `POST /voice/transcribe` — принять `audio_base64`, локально получить
  transcript и записать `voice.transcript.recorded` в audit-chain.
- `POST /voice/retention/cleanup` — удалить исходное аудио tenant, у которого
  истёк TTL.

`create_voice_to_chain_app` собирает FastAPI-приложение на общем
`ServiceTemplateConfig`. В production по умолчанию используется
`WhisperCppCliTranscriber`; в unit/acceptance-тестах доступен
`InMemoryWhisperCppTranscriber`.

## Безопасность

- В blockchain audit передаются только SHA256-хэши и безопасные metadata:
  `transcript_sha256`, `audio_sha256`, `audio_id`, модель и срок удаления
  исходника.
- Transcript, raw voice bytes, `text`, token-like поля и ПДн не записываются в
  chain metadata; проверка дополнительно выполняется Private Blockchain Auditor.
- Исходное аудио хранится во временном tenant-scoped хранилище и удаляется
  TTL-очисткой в пределах 24 часов.

## Настройки

| Переменная | Назначение |
|------------|------------|
| `VOICE_RAW_AUDIO_TTL_HOURS` | TTL исходного аудио, максимум 24 часа |
| `WHISPER_CPP_BINARY_PATH` | путь к `whisper-cli` или совместимому бинарю |
| `WHISPER_CPP_MODEL_PATH` | путь к локальной модели Whisper.cpp |
| `WHISPER_CPP_LANGUAGE` | язык по умолчанию |
| `WHISPER_CPP_TIMEOUT_SECONDS` | timeout CLI-транскрипции |
