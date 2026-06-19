# Unified Messenger Adapter

**Статус:** каркас сервиса, реализация запланирована в этапе 2.

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

## Связанные документы

- [Спецификация модуля](../../docs/modules/messenger-adapter.md)
- [Контракты событий](../../docs/contracts/events.md)
- [Безопасность](../../docs/SECURITY.md)
