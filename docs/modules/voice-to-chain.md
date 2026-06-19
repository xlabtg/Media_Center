# Voice-to-Chain Module

**Статус:** 🟢 реализовано · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:voice-to-chain`

Голос → локальная транскрипция (Whisper.cpp) → хэш транскрипта в блокчейн; исходное аудио автоматически удаляется в пределах 24 ч.

## Зона ответственности
- Приём голосового ввода и локальная транскрипция через Whisper.cpp
- Фиксация SHA256-хэша результата в блокчейн-аудит
- Автоматическое удаление исходного аудио (≤ 24 ч)

## Основные интерфейсы
- **POST** `/voice/transcribe` — отправить аудио, получить транскрипт и хэш

## Зависимости
- Whisper.cpp (локально), Private Blockchain Auditor
- Объектное хранилище (временное, с TTL)

## Реализовано в issue #59
- `create_voice_to_chain_app` собирает FastAPI-сервис с endpoint
  `POST /voice/transcribe` для локальной транскрипции через Whisper.cpp-compatible
  adapter.
- `WhisperCppCliTranscriber` запускает локальный `whisper-cli`, а
  `InMemoryWhisperCppTranscriber` фиксирует тот же контракт в тестах без внешней
  модели.
- Для события `voice.transcript.recorded` рассчитывается SHA256 audit hash,
  который записывается в Private Blockchain Auditor.
- В blockchain metadata передаются только hash-only поля: `transcript_sha256`,
  `audio_sha256`, `audio_id`, модель и `raw_audio_expires_at`; transcript, raw
  voice и text не попадают в chain.
- Исходное аудио хранится во временном tenant-scoped хранилище; TTL-очистка
  `POST /voice/retention/cleanup` удаляет его не позднее 24 часов.

## Безопасность и мультитенантность
- Транскрипция выполняется локально (данные не покидают периметр)
- Исходное аудио удаляется автоматически (минимизация ПДн, ФЗ-152)
- Прямой доступ к Private Blockchain Auditor остаётся council-only; сервис
  пишет hash-only запись через внутренний tenant-scoped service context.

## Связанные задачи (issue)
- [#59](https://github.com/xlabtg/Media_Center/issues/59) — Voice-to-Chain: Whisper.cpp + авто-удаление аудио (24 ч) (`type:feature`)
- [#72](https://github.com/xlabtg/Media_Center/issues/72) — UI голосового ассистента (`type:feature`)

## Связанные документы
- [COMPLIANCE.md](../COMPLIANCE.md)
- [SECURITY.md](../SECURITY.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Спецификация синхронизирована с реализацией Voice-to-Chain для issue #59.</sub>
