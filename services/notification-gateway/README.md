# Notification Gateway

Сервис отправляет tenant-scoped уведомления участникам и Совету по событиям
вклада, выплат, вето и задач. Контракт реализован для #62 и рассчитан на
раннюю интеграцию с RabbitMQ-событиями и будущими внешними каналами доставки.

## Интерфейсы

- `create_notification_gateway_app(config)` собирает FastAPI-приложение
  Notification Gateway.
- `POST /notify` принимает событие, список получателей, каналы и шаблон,
  рендерит уведомления и отправляет их через `NotificationChannel`.
- `GET /notify/preferences` возвращает настройки доставки текущего получателя
  или получателя, которым управляет Совет.
- `PUT /notify/preferences` обновляет включённость, каналы, подписки на типы
  событий и template override для получателя.
- `message_purpose` в `POST /notify` помечает email как `system` или
  `marketing`, а `metadata.email_recipients` связывает `recipient_id` с
  tenant-scoped адресом доставки.

## Реализованный слой

`InMemoryNotificationRepository` хранит настройки получателей и шаблоны в
памяти, ключуя их по `tenant_id`. Это контрактная реализация для локальных
тестов и ранней интеграции; production-хранилище может заменить репозиторий без
изменения HTTP API.

`InMemoryNotificationChannel` фиксирует доставку по каналам `telegram`,
`email`, `webhook` или любому другому нормализованному channel name. Внешние
коннекторы подключаются через protocol `NotificationChannel`.

Email-доставка для #297 выделена в `notification_gateway.email_delivery`:

- `InMemoryEmailOutboxRepository` хранит outbox сообщений и маршруты
  `EmailProviderRoute` отдельно по `tenant_id`.
- `EmailProviderRoute` выбирает provider adapter по `purpose`, `priority` и
  статусу `active|paused|disabled`; отключённые marketing/system пути не
  отправляют сообщение, но запись остаётся в outbox со статусом `deferred`.
- `EmailDeliveryService` создаёт email-сообщение из отрендеренного
  `NotificationDeliveryCommand`, сохраняет его в outbox и пытается отправить
  через зарегистрированный `EmailProviderAdapter`.
- `InMemoryEmailProviderAdapter` покрывает локальные тесты. Для Postalserver,
  Mailgun или другого REST-провайдера production adapter подключается через тот
  же protocol и может менять endpoint/region/credentials без изменения тела
  уже созданного сообщения.
- `sender_alias` хранится на route, поэтому outbox-сообщение не привязано к
  конкретному provider sender address и может быть переотправлено через другой
  route.

## Безопасность

- Все endpoint требуют JWT tenant context и `X-Tenant-Id`.
- Отправка уведомлений доступна ролям `council`, `presidium`, `board`.
- Участники могут читать и менять только собственные preferences; роли
  `council`, `presidium`, `board` могут управлять получателями tenant.
- Событие `notification.dispatched` содержит delivery ids, channel names,
  recipient hash и счётчики, но не содержит rendered subject/body и контекст
  шаблона.
- tenant-isolation контракт #62: настройки и шаблоны не смешиваются между
  tenant, а подмена `X-Tenant-Id` возвращает `403 tenant_isolation_violation`.
