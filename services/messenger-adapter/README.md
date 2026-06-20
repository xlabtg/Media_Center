# Unified Messenger Adapter

**Статус:** реализован минимальный контур Unified Messenger Adapter этапа 2.

## Назначение

Unified Messenger Adapter предоставляет единый интерфейс публикации на
Telegram, VK, Dzen, OK и другие площадки. Сервис трансформирует контент под
ограничения площадки, инъектирует ссылки и управляет ретраями.

## Границы ответственности

- владеет реестром площадок, токенами публикации и publication jobs;
- реализует площадочные адаптеры за единым интерфейсом;
- не генерирует контент и не рассчитывает вклад самостоятельно;
- шифрует и минимизирует чувствительные данные интеграций.

## Базовый адаптер

Пакет `messenger_adapter` содержит фундамент для будущих площадочных
интеграций:

- `UnifiedMessengerAdapter` принимает единый `PublicationBatchRequest`,
  выбирает активные площадки tenant'а из `Platform Registry` по приоритету,
  резолвит `target_id` из запроса или `parameters.default_target_id` и
  публикует материал через зарегистрированные площадочные адаптеры;
- `BasePlatformAdapter` принимает единый `PublicationRequest`, получает токен
  tenant/platform из хранилища и вызывает конкретный `PlatformPublisher`;
- `RetryPolicy` повторяет временные сбои с экспоненциальной задержкой и
  фиксирует финальный результат в событиях `publication.succeeded` /
  `publication.failed`;
- `PlatformTokenCipher` шифрует токены AES-256-GCM с привязкой associated data
  к `tenant_id` и `platform`, а `InMemoryPlatformTokenStore` не выдаёт токены
  между tenant'ами;
- события и audit metadata содержат только служебные идентификаторы, хэши
  внешних ссылок и количество попыток, без raw platform token.

## Адаптеры Telegram и VK

- `TelegramBotApiPublisher` публикует текстовые материалы через Telegram Bot
  API `sendMessage`, нормализует `429`/`parameters.retry_after` в
  `rate_limited` и возвращает platform reference вида `chat_id:message_id`.
- `VKWallPublisher` публикует материалы через VK API `wall.post`, передаёт
  токен в form body, нормализует ошибки VK `6`/`9` как `rate_limited`, а
  ошибки авторизации и доступа как неретраемые `auth_failed`/`access_denied`.
- Оба publisher-а используются через `BasePlatformAdapter`, поэтому шифрование
  токенов, tenant isolation, retry policy, события и audit остаются едиными для
  всех площадок.

## VK API

- `VKWallPublisher` поддерживает production-контур публикации в VK API 5.199:
  `wall.post`, `attachments`, `from_group`, `guid` и другие разрешённые поля
  берутся из `PublicationRequest.metadata["vk"]`, а raw access token остаётся
  внутри `PlatformPublishCommand`.
- `VKAPIRateLimiter` добавляет локальный tenant/target/action scoped pacing
  перед вызовами `wall.post`, `stats.getPostReach` и `wall.getById`; ответы VK
  с rate limit дополнительно нормализуются в общий `rate_limited` для retry
  policy `BasePlatformAdapter`.
- `VKPostMetricsCollector` собирает показатели поста через
  `stats.getPostReach` и `wall.getById`: `reach_total`, reach по подписчикам,
  views, likes, comments, reposts, clicks (`links`), переходы в группу,
  вступления, скрытия, жалобы и отписки.
- Результаты `VKPostMetricsBatch` содержат tenant-scoped SHA-256 хэши target и
  platform ref, агрегированные счётчики и ошибки по `post_id`; raw token и
  исходный текст публикации в них не попадают.

## Трансформация контента и адаптеры Dzen/OK

- `PlatformContentTransformer` обрезает текст и список медиа по лимитам
  площадки, ограничивает количество ссылок и добавляет служебную метку
  `content_transform` в metadata без сырого токена.
- `BasePlatformAdapter` может принимать `content_transformer`, поэтому
  площадочные publisher-ы получают уже адаптированный `PublicationRequest`.
- `DzenPostPublisher` публикует материал в Dzen endpoint с OAuth-заголовком,
  передаёт `channel_id`, текст, title/tags и media из metadata, нормализует
  `429`, auth/access и временные сбои в общий `PlatformPublicationError`.
- `OKMediatopicPublisher` публикует OK `mediatopic.post`, собирает attachment
  из текста и media, поддерживает подпись `sig` через application secret и
  мапит rate limit/auth/access/server ошибки в единый контракт адаптера.

## Дополнительные площадки top-10 РФ

- `RegistryHTTPPublisher` подключает дополнительные площадки через
  `PlatformRegistryEntry.parameters["http"]`: endpoint, HTTP method, auth mode,
  поля target/content/media и поля ответа задаются декларативно.
- Для пилотного расширения top-10 РФ предусмотрены registry-managed площадки
  `rutube`, `vc`, `pikabu`, `habr`, `tenchat` и `livejournal`; полный каталог
  и регулярное обновление статусов вынесены в задачу #80.
- Ошибки дополнительных площадок приводятся к общему контракту
  `PlatformPublicationError`, поэтому `UnifiedMessengerAdapter` может вернуть
  частичный успех: receipts по Dzen/OK/rutube и failures, например по habr
  rate limit, без остановки всей batch-публикации.
- Raw platform token остаётся внутри `PlatformPublishCommand`; наружу выходят
  только receipt/failure metadata, audit hash и tenant-scoped platform refs.

## Platform Registry и реферальные ссылки

- `PlatformRegistryEntry` задаёт tenant-scoped параметры площадки: статус,
  приоритет, лимиты контента и декларативные `parameters`.
- Для issue #80 пакет экспортирует `DEFAULT_PLATFORM_CATALOG_SIZE`,
  `default_platform_registry_entries()` и `build_default_platform_registry()`:
  seed создаёт 102 tenant-scoped записи с лимитами, статусами,
  `parameters.default_target_id`, категориями и стабильными приоритетами.
- `InMemoryPlatformRegistry` хранит записи по паре `tenant_id/platform`; если
  площадка отсутствует, paused или disabled, `BasePlatformAdapter` останавливает
  публикацию до lookup токена и вызова внешнего publisher-а.
- `InMemoryPlatformRegistry.update_status()` актуализирует доступность площадки;
  `UnifiedMessengerAdapter` при batch-публикации без явного списка платформ
  маршрутизирует только `active` записи и сохраняет порядок приоритетов
  заполненного каталога.
- Когда `BasePlatformAdapter` получает `platform_registry`, лимиты из реестра
  становятся источником для `PlatformContentTransformer`.
- `ReferralLinkInjector` читает `metadata["referral_route"]`, дополняет его
  `tenant_id` и `content_id`/`publication_id`, вызывает CGLR
  `generate_referral_links` и добавляет ссылочный блок в текст публикации.
- В audit metadata попадает только компактный список `referral_links`
  (`level`, `owner_id`, `reward_share`), без platform token и без секретов.

## Устойчивость интеграций

- `ResilientPlatformPublisher` подключается как обычный `PlatformPublisher` для
  `BasePlatformAdapter`, поэтому существующие retry policy, audit log и события
  сохраняются для primary и fallback-доставки.
- Перед primary-вызовом publisher получает tenant/platform scoped proxy lease и
  добавляет в команду только `proxy lease metadata`: `lease_id`, `proxy_id`,
  protocol и hash redacted URL. Raw proxy endpoint, `secret_ref` и platform
  token не выходят за границу интеграционного слоя.
- Если primary publisher возвращает retryable сбой (`platform_unavailable`,
  `platform_timeout`, `rate_limited`), контур пробует разрешённые
  IPFS/TON/Matrix fallback routes в порядке приоритета.
- Fallback routes хранят `endpoint` и `secret_ref` внутри registry, а публичный
  `FallbackPublicationResult` отдаёт только `gateway_ref_hash`, `content_hash`,
  `endpoint_hash`, `secret_ref_hash`, тип канала и идентификатор route.
- Недоступный fallback route помечается `unhealthy`, после чего автоматическое
  переключение идёт к следующему каналу без раскрытия исходного текста
  публикации, токена площадки или секретов канала.

## Telegram-клиент участника (issue #71)

- `TelegramClientGateway` даёт участникам входящий канал работы через Telegram:
  связывает аккаунт (`link_account`), разбирает базовые команды (`/start`,
  `/help`, `/status`, `/balance`, `/tasks`) и формирует ответы на русском без
  раскрытия секретов.
- `TelegramIdentityCipher` шифрует Telegram-идентичность участника AES-256-GCM
  с доменно-разделённой меткой AAD `telegram_client_identity`; в события, аудит
  и логи попадает только шифртекст и tenant-scoped `telegram_user_ref_hash`, а
  не сырой идентификатор и не данные участника (баллы, статус).
- `TelegramProxyRotator` обеспечивает работу через прокси-ротацию: tenant-scoped
  пул `http`/`socks5`/`mtproto`, round-robin по живым endpoint'ам, пометку
  здоровья и `redacted_url`/SHA-256 хэши вместо учётных данных (хранятся только
  как `secret_ref`).
- Взаимодействия публикуют события `messenger.telegram_client.account_linked` и
  `messenger.telegram_client.command_handled` и фиксируются аудит-хэшем.

## Telegram через Telethon

- `TelegramTelethonPublisher` подключается как `PlatformPublisher` для
  `BasePlatformAdapter`, поэтому публикации через Telethon используют тот же
  retry policy, audit log и события `publication.succeeded` /
  `publication.failed`, что и Bot API/VK/Dzen/OK.
- `FloodWait` и похожие Telethon rate-limit ошибки мапятся в общий
  `PlatformPublicationError(error_code="rate_limited")` с `retry_after_seconds`,
  а локальный `TelegramTelethonRateLimiter` ограничивает частоту
  `send_message` по tenant/session/target.
- `InMemoryTelegramTelethonSessionStore` хранит `StringSession` только в
  AES-256-GCM шифртексте с AAD `telegram_telethon_session`; platform token в
  `BasePlatformAdapter` используется как `session_ref`, а не как raw session.
- `TelegramTelethonSessionClientProvider` лениво создаёт реальный Telethon
  клиент через `TelethonClientFactory`, проверяет авторизацию сессии и
  сохраняет обновлённый `StringSession` после успешного взаимодействия.
- `TelegramTelethonInboundBridge` читает сообщения из Telegram через Telethon,
  вызывает `TelegramClientGateway` и отправляет ответ участнику, не включая raw
  текст команды, raw Telegram ID или session string в публичные результаты.

## Связанные документы

- [Спецификация модуля](../../docs/modules/messenger-adapter.md)
- [Контракты событий](../../docs/contracts/events.md)
- [Безопасность](../../docs/SECURITY.md)
