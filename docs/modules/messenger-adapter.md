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

## Telegram через Telethon (issue #75)
- `TelegramTelethonPublisher` реализует исходящую публикацию через Telethon
  поверх существующего `BasePlatformAdapter`: общий retry/audit/event-контур
  сохраняется, а `FloodWait`/`retry_after` Telegram нормализуется в
  `rate_limited` с задержкой повтора.
- `InMemoryTelegramTelethonSessionStore` и
  `TelegramTelethonSessionClientProvider` задают production-ready контракт для
  защищённого хранения `StringSession`: raw session string шифруется
  AES-256-GCM с tenant-scoped AAD `telegram_telethon_session`, наружу попадают
  только `session_ref` и SHA-256 хэши.
- `TelegramTelethonRateLimiter` применяет tenant/session/target scoped pacing
  перед `send_message`, чтобы не создавать локальные retry storm и соблюдать
  лимиты Telegram поверх серверных `FloodWait`.
- `TelegramTelethonInboundBridge` читает сообщения через Telethon, переводит их
  в `TelegramInboundMessage`, передаёт в `TelegramClientGateway` и отправляет
  ответ через ту же сессию без публикации raw текста команды, raw Telegram ID
  или session string в результатах.
- Acceptance-контракт issue #75 покрывает публикацию с retry после FloodWait,
  чтение и ответ на `/balance`, pacing лимитов и tenant-scoped шифрование
  Telethon-сессий.

## VK API (issue #76)
- `VKWallPublisher` реализует production-контур публикации через VK API 5.199:
  метод `wall.post`, поля `attachments`, `from_group`, `guid`,
  delayed/signed/ads metadata и общий маппинг ошибок VK в
  `PlatformPublicationError`.
- `VKAPIRateLimiter` применяет локальный tenant/target/action scoped pacing до
  вызовов VK API, чтобы не провоцировать retry storm; серверные лимиты VK
  (`6`, `9`, `29`, HTTP `429`) нормализуются в `rate_limited` и учитываются
  общей retry policy.
- `VKPostMetricsCollector` собирает метрики опубликованных записей через
  `stats.getPostReach` и `wall.getById`: `reach_total`,
  `reach_subscribers`, viral/ads reach, views, likes, comments, reposts,
  clicks (`links`), переходы в группу, вступления, скрытия, жалобы и отписки.
- `VKPostMetricsBatch` возвращает только агрегированные счётчики, ошибки по
  `post_id`, tenant-scoped SHA-256 хэши target/platform ref и служебные
  timestamps; raw VK token, исходный текст публикации и raw target ref в
  результате не раскрываются.
- Acceptance-контракт issue #76 покрывает публикацию `wall.post`, сбор метрик
  поста, локальный pacing лимитов и отсутствие raw token/content в публичных
  результатах.

## Dzen/OK и дополнительные площадки top-10 РФ (issue #77)
- Dzen и OK остаются специализированными publisher-ами
  `DzenPostPublisher`/`OKMediatopicPublisher`, а дополнительные площадки
  подключаются через `RegistryHTTPPublisher` без изменения
  `UnifiedMessengerAdapter`.
- `RegistryHTTPPublisher` читает tenant-scoped `PlatformRegistryEntry` и
  требует активный статус площадки; HTTP-контур задаётся декларативно в
  `parameters.http.endpoint_url`, `method`, `auth_mode`, `target_field`,
  `content_field`, `media_field`, `response_ref_fields` и связанных полях.
- Для дополнительных площадок top-10 РФ на пилотном контуре предусмотрены
  registry-managed профили `rutube`, `vc`, `pikabu`, `habr`, `tenchat` и
  `livejournal`; полный каталог и актуализация статусов остаются задачей #80.
- Ошибки HTTP-интеграций нормализуются в общий `PlatformPublicationError`:
  `rate_limited`, `auth_failed`, `access_denied`, `platform_unavailable`,
  `invalid_request` и ошибки конфигурации реестра, поэтому batch-публикация
  возвращает частичные failures без раскрытия raw platform token.
- Acceptance-контракт issue #77 покрывает batch через Dzen, OK и
  registry-configured HTTP-площадки, приоритеты реестра, default target id,
  обработку rate limit и отсутствие токенов в публичном результате.

## Зависимости
- CGLR (реферальные ссылки), Contribution Ledger
- Telethon 1.44.0 (Telegram), VK API, политики ретраев и резервные разрешенные
  каналы

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
<sub>Спецификация синхронизирована с реализацией Unified Messenger Adapter для issue #48, Telegram-клиента участника для issue #71, сквозным stage-4 acceptance contract #74, Telethon-интеграцией issue #75, VK API-интеграцией issue #76 и Dzen/OK/top-10 интеграциями issue #77.</sub>
