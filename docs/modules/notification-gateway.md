# Notification Gateway

**Статус:** 🟢 реализовано для #62 · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:notification`

Единый шлюз уведомлений о ключевых событиях (вклад, выплаты, вето, задачи) по нескольким каналам.

## Зона ответственности
- Подписка на события и шаблоны уведомлений
- Доставка по нескольким каналам (мессенджеры, e-mail и т. п.)
- Настройки получателя и изоляция по тенанту

## Основные интерфейсы
- **POST** `/notify` — отправить уведомления по событию, tenant-шаблону,
  списку получателей и разрешённым каналам
- **GET/PUT** `/notify/preferences` — настройки доставки получателя:
  включённость, каналы, подписки на event type и template overrides

## Реализованный контракт #62
- `NotificationGateway` собирает событие, шаблон, preferences получателей и
  доставляет уведомления через protocol `NotificationChannel`.
- `create_notification_gateway_app` предоставляет FastAPI endpoint
  `POST /notify`, `GET /notify/preferences` и `PUT /notify/preferences`.
- `InMemoryNotificationRepository` хранит preferences и templates с ключом
  `tenant_id`, что фиксирует контракт ранней интеграции без production БД.
- Каналы задаются в запросе, шаблоне и preferences; итоговая доставка идёт по
  пересечению разрешённых каналов получателя и каналов события.
- Шаблоны настраиваются через payload `template` в `POST /notify`; preferences
  могут выбирать template override для конкретного `event_type`.
- Событие `notification.dispatched` публикуется без rendered subject/body и без
  контекста шаблона: наружу уходят только delivery ids, каналы, recipient hash
  и счётчики.
- tenant-isolation контракт #62: настройки и шаблоны другого tenant не
  участвуют в доставке, а подмена `X-Tenant-Id` возвращает
  `403 tenant_isolation_violation`.

## Email outbox и provider routing (#297)
- `message_purpose` в `POST /notify` разделяет системные и marketing email:
  текущий контракт поддерживает `system` и `marketing`.
- `metadata.email_recipients` задаёт адреса доставки по `recipient_id`; это
  оставляет публичный список recipients tenant-scoped и не заставляет
  использовать email как `SubjectId`.
- `InMemoryEmailOutboxRepository` хранит email-сообщение независимо от
  провайдера: subject/body/content hash, purpose, sender alias, статус outbox и
  provider ref hash после отправки.
- `EmailProviderRoute` выбирает provider adapter по `tenant_id`, purpose,
  priority и статусу `active|paused|disabled`. Отключённый путь не отправляет
  письмо, но сообщение остаётся в outbox со статусом `deferred`.
- `EmailProviderAdapter` отделяет генерацию письма от Postalserver, Mailgun или
  другого REST API; смена endpoint, region или credentials происходит через
  route без изменения уже созданного сообщения.

## Зависимости
- RabbitMQ (события), Unified Messenger Adapter (каналы)

## Безопасность и мультитенантность
- Уведомления и настройки изолированы по `tenant_id`

## Связанные задачи (issue)
- [#62](https://github.com/xlabtg/Media_Center/issues/62) — Notification Gateway: уведомления участников и Совета (`type:feature`)

## Реализация
- [services/notification-gateway/notification_gateway/api.py](../../services/notification-gateway/notification_gateway/api.py) —
  REST API, preferences, templates, tenant-scoped dispatch и event contract
- [services/notification-gateway/notification_gateway/email_delivery.py](../../services/notification-gateway/notification_gateway/email_delivery.py) —
  email outbox, purpose routing и provider adapter protocol для #297
- [services/notification-gateway/README.md](../../services/notification-gateway/README.md) —
  запуск, границы сервиса и безопасность
- [tests/test_notification_gateway_issue62_acceptance_contract.py](../../tests/test_notification_gateway_issue62_acceptance_contract.py) —
  acceptance и tenant-isolation контракт #62
- [tests/test_notification_gateway_issue297_email_contract.py](../../tests/test_notification_gateway_issue297_email_contract.py) —
  acceptance контракт email outbox/provider routing для #297

## Связанные документы
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Спецификация синхронизирована с реализацией Notification Gateway для issue #62 и email outbox/provider routing для #297.</sub>
