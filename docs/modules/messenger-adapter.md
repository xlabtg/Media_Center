# Unified Messenger Adapter

**Статус:** 🟢 реализовано · **Этап:** Этап 2 — Ключевые микросервисы · **Компонент:** `component:messenger-adapter`

Единый интерфейс публикации в мессенджеры и соцсети РФ с ретраями, шифрованием токенов и трансформацией контента под площадку.

## Зона ответственности
- Единый интерфейс публикации поверх разных площадок (`base_adapter`)
- Адаптеры Telegram, VK, Dzen, OK и др. (top-10 РФ)
- Трансформация и обрезка контента под ограничения площадки
- Реестр площадок (Platform Registry) и инъекция реферальных ссылок
- Клиентский (входящий) канал работы участника через Telegram
  (`telegram_client`)

## Основные интерфейсы
- **POST** `/publish` — опубликовать контент на площадку(и)
- **GET** `/platforms` — список и статусы площадок тенанта

## Модель данных (черновик)
- **platform_registry** — `tenant_id`, `platform`, `limits`, `priority`, `status`
- **platform_tokens** — `tenant_id`, `platform`, `token_encrypted` (AES-256)

## Platform Registry
- Реестр хранит tenant-scoped записи площадок: `platform`, лимиты контента,
  приоритет, статус (`active`, `paused`, `disabled`) и декларативные параметры
  интеграции.
- `UnifiedMessengerAdapter` принимает `PublicationBatchRequest`, строит
  batch-публикацию по активным записям реестра в порядке приоритета, использует
  `parameters.default_target_id` как fallback для цели публикации и возвращает
  единый результат с receipts и failures по площадкам.
- Публикация через `BasePlatformAdapter` может быть привязана к реестру: запись
  должна существовать и иметь статус `active`; иначе адаптер возвращает
  неретраемый `PlatformPublicationError` до обращения к токенам и внешним API.
- Лимиты из активной записи реестра используются как источник для трансформации
  контента перед отправкой в площадочный publisher.

## Инъекция реферальных ссылок
- `ReferralLinkInjector` принимает route payload в
  `PublicationRequest.metadata["referral_route"]`.
- Payload совместим с CGLR `link_rotator`: `admin_link`, `author_link`,
  `l3_candidates`, опциональные `content_id`, `rotation_seed` и
  `l3_min_contribution_weight`.
- Если `content_id` не передан в route payload, используется
  `metadata["content_id"]` или `publication_id`.
- В текст публикации добавляется блок `Реферальные ссылки` с L1/L2/L3 URL, а в
  metadata сохраняется компактный список уровней, владельцев и reward share.

## Telegram-клиент участника (issue #71)
- `TelegramClientGateway` — оркестратор входящего канала: связывает Telegram-
  аккаунт участника, разбирает базовые команды и фиксирует аудит и события без
  раскрытия секретов. Базовые сценарии: `/start`, `/help`, `/status`,
  `/balance` (баллы и Кв), `/tasks`; нераспознанные команды отдают подсказку.
- **Шифрование чувствительных данных.** `TelegramIdentityCipher` переиспользует
  AES-256-GCM (`PlatformTokenCipher`) с доменно-разделённой меткой AAD
  `telegram_client_identity`, поэтому сырой Telegram ID нигде не хранится в
  открытом виде. В события, аудит и логи попадает только шифртекст и
  детерминированный `telegram_user_ref_hash` (tenant-scoped SHA-256).
- **Работа через прокси.** `TelegramProxyRotator` ведёт tenant-scoped пул
  endpoint'ов (`http`/`socks5`/`mtproto`), выбирает живой proxy по round-robin,
  поддерживает пометку здоровья (`mark_unhealthy`/`mark_healthy`) и
  сигнализирует `TelegramProxyUnavailableError` при отсутствии живых proxy.
  Учётные данные хранятся только через `secret_ref`; наружу отдаются
  `redacted_url` и SHA-256 хэши (`url_hash`, `secret_ref_hash`).
- **События.** `messenger.telegram_client.account_linked` (связка аккаунта) и
  `messenger.telegram_client.command_handled` (обработка команды) публикуются
  через общий конверт событий с `audit_hash` и хэшами вместо секретов.
- Acceptance-контракт issue #71 покрывает доступность базовых сценариев,
  защищённую передачу идентичности (per-tenant AAD, отсутствие сырого ID и
  баланса в событиях) и ротацию прокси с health-failover и изоляцией пулов по
  tenant.
- Сквозной stage-4 acceptance contract для epic #74 проверяет Telegram-клиент
  как часть единого UX-пакета: команда `/balance` проходит через
  `TelegramClientGateway`, получает proxy lease и не раскрывает сырой Telegram
  ID в публичной модели ответа.

## Зависимости
- CGLR (реферальные ссылки), Contribution Ledger
- Telethon (Telegram), VK API, политики ретраев и резервные разрешенные каналы

## Безопасность и мультитенантность
- Токены площадок шифруются (AES-256) и изолированы по `tenant_id`
- Сбои публикации повторяются по политике ретраев с экспоненциальной задержкой
- Acceptance-контракт issue #48 покрывает единый batch-вызов через registry,
  инъекцию CGLR-ссылок, трансформацию контента, публикацию минимум в Telegram и
  VK и отсутствие raw platform token в публичных моделях результата.

## Связанные задачи (issue)
- [#44](https://github.com/xlabtg/Media_Center/issues/44) — base_adapter: интерфейс, ретраи, шифрование токенов (`type:feature`)
- [#45](https://github.com/xlabtg/Media_Center/issues/45) — Адаптеры Telegram и VK (`type:feature`)
- [#46](https://github.com/xlabtg/Media_Center/issues/46) — Адаптеры Dzen, OK + трансформация и обрезка контента (`type:feature`)
- [#47](https://github.com/xlabtg/Media_Center/issues/47) — Platform Registry + инъекция реферальных ссылок + тесты (`type:feature`)
- [#48](https://github.com/xlabtg/Media_Center/issues/48) — 📤 Unified Messenger Adapter (`type:epic`)
- [#71](https://github.com/xlabtg/Media_Center/issues/71) — Telegram-клиент (шифрование, прокси) (`type:feature`)
- [#74](https://github.com/xlabtg/Media_Center/issues/74) — Этап 4 — Клиентские приложения и UX (`type:epic`)
- [#75](https://github.com/xlabtg/Media_Center/issues/75) — Интеграция Telegram (Telethon) (`type:feature`)
- [#76](https://github.com/xlabtg/Media_Center/issues/76) — Интеграция VK API (`type:feature`)
- [#77](https://github.com/xlabtg/Media_Center/issues/77) — Интеграции Dzen, OK и др. (top-10 РФ) (`type:feature`)
- [#80](https://github.com/xlabtg/Media_Center/issues/80) — Реестр 102 площадок и приоритизация (`type:feature`)

## Связанные документы
- [COMPLIANCE.md](../COMPLIANCE.md)
- [SECURITY.md](../SECURITY.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)
- [Acceptance snapshot этапа 4](../STAGE_4_ACCEPTANCE.md)

---
<sub>Спецификация синхронизирована с реализацией Unified Messenger Adapter для issue #48, Telegram-клиента участника для issue #71 и сквозным stage-4 acceptance contract #74.</sub>
