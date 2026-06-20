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

## Platform Registry и реферальные ссылки

- `PlatformRegistryEntry` задаёт tenant-scoped параметры площадки: статус,
  приоритет, лимиты контента и декларативные `parameters`.
- `InMemoryPlatformRegistry` хранит записи по паре `tenant_id/platform`; если
  площадка отсутствует, paused или disabled, `BasePlatformAdapter` останавливает
  публикацию до lookup токена и вызова внешнего publisher-а.
- Когда `BasePlatformAdapter` получает `platform_registry`, лимиты из реестра
  становятся источником для `PlatformContentTransformer`.
- `ReferralLinkInjector` читает `metadata["referral_route"]`, дополняет его
  `tenant_id` и `content_id`/`publication_id`, вызывает CGLR
  `generate_referral_links` и добавляет ссылочный блок в текст публикации.
- В audit metadata попадает только компактный список `referral_links`
  (`level`, `owner_id`, `reward_share`), без platform token и без секретов.

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

## Связанные документы

- [Спецификация модуля](../../docs/modules/messenger-adapter.md)
- [Контракты событий](../../docs/contracts/events.md)
- [Безопасность](../../docs/SECURITY.md)
