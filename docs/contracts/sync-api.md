# Синхронные REST/gRPC контракты

Документ фиксирует baseline синхронного взаимодействия сервисов НМЦ. Полные
OpenAPI-схемы будут добавляться в задачах реализации соответствующих сервисов.

## 1. Точка входа

Внешние клиенты обращаются только к **API Gateway**. Gateway выполняет:

- проверку JWT HS256;
- извлечение `tenant_id`, ролей и user context;
- RBAC и rate limiting;
- tenant-aware маршрутизацию к сервисам;
- нормализацию ошибок и `correlation_id`.

Прямой доступ к сервисам разрешён только внутри приватной сервисной сети и
требует service-to-service credentials.

## 1.1. Discovery endpoint'ов

Вопрос service discovery закрыт в
[SERVICE_DISCOVERY.md](../SERVICE_DISCOVERY.md) для issue #295. Сервис не
строит адрес downstream из `SERVICE_NAME`, `X-Service-Name` или S2S headers:
endpoint из env/Helm values является source of truth, а DNS текущего рантайма
резолвит host.

Локально используется Docker Compose DNS: инфраструктура доступна как
`postgres:5432`, `redis:6379`, `rabbitmq:5672`, `http://minio:9000`,
`http://otel-collector:4318`, а product service - как
`http://<service>:7700`. В Kubernetes каждый product service получает
`Kubernetes Service` типа `ClusterIP` с именем
`<release>-media-center-<service>`. S2S credentials не являются механизмом
discovery: они нужны для авторизации внутреннего вызова после выбора endpoint.

## 2. Общие headers

| Header | Обязателен | Назначение |
|--------|------------|------------|
| `Authorization: Bearer <jwt>` | Да для клиентских запросов | Источник ролей, user id и `tenant_id`. |
| `X-Correlation-Id` | Да | Сквозная трассировка запроса, событий и аудита. |
| `Idempotency-Key` | Да для create/execute команд | Защита от повторного исполнения side effects. |
| `X-Service-Name` | Да для внутренних вызовов | Идентификация вызывающего сервиса. |
| `X-Tenant-Id` | Только internal trusted | Проброс tenant context от Gateway; клиенты не задают вручную. |
| `X-Subject-Id` | Только internal trusted | Проверенный `sub` из access JWT. |
| `X-Actor-Roles` | Только internal trusted | Проверенные роли из access JWT для downstream authorization/audit. |
| `X-Forwarded-Prefix` | Только internal trusted | Публичный service prefix, срезанный Gateway перед вызовом downstream. |
| `X-Original-Path` | Только internal trusted | Исходный path внешнего запроса для трассировки и аудита. |

## 3. Формат ошибок

```json
{
  "error": {
    "code": "tenant_isolation_violation",
    "message": "Доступ к ресурсу другого tenant запрещён",
    "details": {},
    "correlation_id": "01HX0000000000000000000000"
  }
}
```

Базовые коды:

| HTTP | `code` | Когда используется |
|------|--------|--------------------|
| 400 | `validation_error` | Неверная схема запроса или недопустимое состояние команды. |
| 401 | `unauthorized` | Нет валидного JWT или service credentials. |
| 403 | `forbidden` | Недостаточно роли/RBAC. |
| 403 | `tenant_isolation_violation` | Ресурс принадлежит другому tenant или tenant context отсутствует. |
| 409 | `idempotency_conflict` | Повторный `Idempotency-Key` с другим payload. |
| 422 | `policy_gate_required` | Операция требует HITL/policy approval перед исполнением. |
| 429 | `rate_limited` | Превышен лимит API Gateway или площадки. |

## 4. Карта синхронных endpoints

### API Gateway

| Метод | Путь | Назначение | Downstream |
|-------|------|------------|------------|
| `POST` | `/auth/login` | Выдать JWT access-token HS256 и opaque refresh-token | Auth boundary |
| `POST` | `/auth/refresh` | Проверить refresh-token, выполнить rotation и вернуть новую пару | Auth boundary |
| `POST` | `/auth/logout` | Отозвать refresh-token текущей сессии | Auth boundary |
| `POST` | `/auth/2fa/totp/setup` | Подготовить TOTP secret/provisioning URI через secret-контур | Auth boundary |
| `POST` | `/auth/2fa/totp/verify` | Подтвердить TOTP для чувствительной операции | Auth boundary |
| `ANY` | `/<service>/...` | Tenant-aware proxy с JWT/RBAC/rate limit | Все сервисы |
| `GET` | `/health` | Состояние Gateway | Локально |

Auth boundary выдаёт access JWT с `typ=access`, `jti`, `tenant_id`, `sub`,
`roles`, `iss`, `aud`, `iat`, `nbf` и `exp`. Refresh-токены не являются JWT:
клиент получает opaque token, а сервер хранит только SHA256-хэш, срок действия,
tenant/user context и состояние отзыва. Успешный refresh всегда отзывает старый
токен; повторное использование старого refresh-token возвращает `401
unauthorized`.

Gateway proxy срезает service prefix перед вызовом downstream. Например,
`GET /contribution-ledger/contributions` вызывает Contribution Ledger с path
`/contributions`, а `tenant_id`, `subject`, роли и correlation context
передаются только через trusted internal headers, сформированные Gateway.
Превышение лимита API Gateway возвращает `429 rate_limited` с `Retry-After` и
`X-RateLimit-*` headers.

### Contribution Ledger & Weight Engine

| Метод | Путь | Команда/запрос | Side effect |
|-------|------|----------------|-------------|
| `POST` | `/contributions` | Зарегистрировать вклад и вернуть начисленные баллы/audit hash | Да |
| `GET` | `/weights?period=YYYY-MM` | Получить `kv_raw`, `kv_capped` и агрегаты за период | Нет |
| `POST` | `/weights/recalculate` | Запустить перерасчёт весов за период | Да |
| `GET` | `/payout-distribution?period=YYYY-MM` | Получить доли распределения для HITL | Нет |

### Content Generator & Link Router

| Метод | Путь | Команда/запрос | Side effect |
|-------|------|----------------|-------------|
| `POST` | `/generate` | Сгенерировать материал по шаблону, политике и input data | Да |
| `GET` | `/content/{content_id}` | Получить готовый материал, L1/L2/L3 ссылки и запись вклада | Нет |
| `POST` | `/content/{content_id}/validate` | Проверить материал под площадку и контентные правила | Да, запланировано |
| `GET` | `/templates` | Список шаблонов tenant | Нет, запланировано |

В первом REST-контуре реализованы `POST /generate` и
`GET /content/{content_id}`. `POST /generate` требует `Idempotency-Key` и
принимает:

- `template_id`, `template_body`, `context`, `validation`;
- `platform_targets` для события `content.generated`;
- `link_routing.admin_link`, `link_routing.author_link`, `l3_candidates`,
  `rotation_seed`;
- `contribution` с `event_type`, `platform`, `reach`, `extra_reach`,
  `occurred_at` и безопасной `metadata`.

Ответ содержит `content_id`, `content`, `content_with_links`, `content_hash`,
массив `links`, `reward_distribution` и объект `contribution`. Логирование
вклада выполняется с `source_type=cglr_generation` и `source_ref=content_id`;
сырой текст материала не попадает в событийный payload.

### Unified Messenger Adapter

| Метод | Путь | Команда/запрос | Side effect |
|-------|------|----------------|-------------|
| `POST` | `/publish` | Создать публикационную задачу на одну или несколько площадок | Да |
| `GET` | `/publications/{id}` | Получить статус публикации и platform references | Нет |
| `GET` | `/platforms` | Реестр площадок tenant и их статус | Нет |
| `PUT` | `/platforms/{platform}` | Обновить tenant-настройки площадки | Да |

### HITL Payout Gateway

| Метод | Путь | Команда/запрос | Side effect |
|-------|------|----------------|-------------|
| `POST` | `/payouts/queue` | Поставить выплату или расчётную долю в HITL-очередь | Да |
| `POST` | `/payouts/{id}/veto` | Наложить вето с причиной | Да |
| `POST` | `/payouts/{id}/confirm` | Подтвердить операцию с 2FA | Да |
| `POST` | `/payouts/{id}/execute` | Исполнить после выполнения HITL-правил | Да |
| `GET` | `/payouts?status=` | Список операций tenant | Нет |

### Private Blockchain Auditor

| Метод | Путь | Команда/запрос | Side effect |
|-------|------|----------------|-------------|
| `POST` | `/audit/record` | Принять hash и metadata для batch-записи | Да |
| `POST` | `/audit/verify` | Проверить canonical payload против hash/block reference | Нет |
| `GET` | `/audit/records/{event_id}` | Найти audit record по `event_id` | Нет |

Низкоуровневый вызов к приватной сети выполняется через gRPC connector внутри
Private Blockchain Auditor. Внешние сервисы не вызывают блокчейн-сеть напрямую.

### Policy Manager и Notification Gateway

| Сервис | Метод | Путь | Назначение |
|--------|-------|------|------------|
| Policy Manager | `GET` | `/policies` | Актуальные политики tenant. |
| Policy Manager | `PUT` | `/policies/{key}` | Изменить политику Советом, создать audit trail. |
| Policy Manager | `GET` | `/policies/{key}/history` | История версий. |
| Notification Gateway | `POST` | `/notify` | Отправить уведомление по событию или команде. |
| Notification Gateway | `GET/PUT` | `/notify/preferences` | Настройки доставки tenant/user. |

## 5. Синхронные workflow

### Учёт вклада

1. Клиент отправляет `POST /contribution-ledger/contributions` через API Gateway.
2. Gateway проверяет JWT, RBAC, `tenant_id` и добавляет `correlation_id`.
3. Ledger рассчитывает баллы, пишет запись, создаёт audit hash.
4. Ledger публикует событие `contribution.recorded` и request на audit-chain.

### Выплата

1. Ledger отдаёт доли через `GET /payout-distribution`.
2. HITL создаёт очередь через `POST /payouts/queue`.
3. Совет вызывает `/veto` или `/confirm` в пределах RBAC; `/confirm` требует
   TOTP-подтверждение операции `payout.confirm`.
4. После выполнения правила HITL Gateway запускает исполнение и audit record.

## 6. Требования к схемам

- Все request/response модели описываются Pydantic v2 и экспортируются в
  OpenAPI при реализации сервиса.
- Даты передаются в ISO 8601 UTC.
- Денежные суммы и платёжные реквизиты не передаются в audit-chain endpoints.
- Поля, содержащие потенциальные ПДн, должны быть явно помечены в schema docs и
  не попадать в логи.
