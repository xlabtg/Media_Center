# Асинхронные события RabbitMQ

Документ фиксирует baseline событийной модели НМЦ. Полная AsyncAPI-схема будет
добавляться при реализации сервисов и очередей.

## 1. RabbitMQ топология

| Объект | Назначение |
|--------|------------|
| Exchange `nmc.events` (`topic`) | Доменные события сервисов. |
| Exchange `nmc.commands` (`topic`) | Асинхронные команды/работы для фоновых обработчиков. |
| Exchange `nmc.dlx` | Dead-letter события после исчерпания ретраев. |
| Queue `<service>.<purpose>` | Очередь конкретного потребителя. |

Routing key:

```text
tenant.<tenant_id>.<domain>.<event_name>
```

Для общих технических событий допускается `tenant.system.<domain>.<event_name>`,
если событие не относится к данным конкретного tenant.

## 2. Envelope события

В коде baseline envelope представлен как `libs.shared.EventEnvelope`. Он
сериализуется в JSON, публикуется в RabbitMQ через `RabbitMQEventBus` и
проверяется unit-тестами без живого брокера через `InMemoryEventBus`.

```json
{
  "event_id": "01HX0000000000000000000000",
  "type": "contribution.recorded",
  "schema_version": "1.0",
  "tenant_id": "tenant-demo",
  "source": "contribution-ledger",
  "correlation_id": "01HX0000000000000000000000",
  "causation_id": "01HX0000000000000000000001",
  "occurred_at": "2026-06-18T12:00:00Z",
  "payload": {}
}
```

| Поле | Обязательность | Правило |
|------|----------------|---------|
| `event_id` | Да | Глобально уникальный и стабильный для идемпотентности. |
| `type` | Да | Доменное имя события в формате `<domain>.<event>`. |
| `schema_version` | Да | Semver major/minor для эволюции payload. |
| `tenant_id` | Да | Tenant context из JWT или trusted internal job. |
| `source` | Да | Сервис-источник. |
| `correlation_id` | Да | Связь с HTTP/gRPC command и логами. |
| `causation_id` | Нет | ID команды или события, вызвавшего текущее. |
| `occurred_at` | Да | UTC ISO 8601. |
| `payload` | Да | Данные события без ПДн, токенов, сумм и сырого контента, если явно не разрешено. |

## 3. Каталог событий ядра

### Contribution Ledger

| Event type | Producer | Consumers | Payload baseline |
|------------|----------|-----------|------------------|
| `contribution.recorded` | Contribution Ledger | Analytics, Notification, Blockchain Auditor | `contribution_id`, `member_id_hash`, `event_type`, `points_awarded`, `audit_hash` |
| `weights.recalculated` | Contribution Ledger | HITL, Analytics | `period`, `total_points`, `members_count`, `calculation_hash` |
| `payout.distribution_ready` | Contribution Ledger | HITL Payout Gateway | `period`, `distribution_id`, `distribution_hash`, `member_count` |
| `audit.record.requested` | Contribution Ledger | Private Blockchain Auditor | `event_type`, `event_id`, `audit_hash`, `metadata` |

### CGLR

| Event type | Producer | Consumers | Payload baseline |
|------------|----------|-----------|------------------|
| `content.generated` | CGLR | Messenger Adapter, Analytics | `content_id`, `template_id`, `content_hash`, `platform_targets` без сырого текста материала |
| `content.validation_failed` | CGLR | Activity Command Center, Notification | `content_id`, `policy_key`, `reason_code` |
| `contribution.record_requested` | CGLR | Contribution Ledger | `content_id`, `member_id_hash`, `contribution_type`, `metadata_hash` |

Первый REST-контур CGLR логирует генерацию через Contribution
Ledger-compatible `ContributionLogger`: после `content.generated` создаются
события `contribution.recorded` и `audit.record.requested` с
`source_type=cglr_generation` и `source_ref=content_id`. Событие
`contribution.record_requested` остаётся целевым асинхронным контрактом для
будущего outbox/consumer варианта.

### Unified Messenger Adapter

| Event type | Producer | Consumers | Payload baseline |
|------------|----------|-----------|------------------|
| `publication.requested` | Unified Messenger Adapter | Messenger workers, Activity Command Center | `publication_id`, `content_id`, `platform`, `priority` |
| `publication.succeeded` | Unified Messenger Adapter | Contribution Ledger, Analytics, Notification | `publication_id`, `platform`, `platform_post_id`, `published_at` |
| `publication.failed` | Unified Messenger Adapter | Notification, Activity Command Center | `publication_id`, `platform`, `error_code`, `retry_count` |

### HITL Payout Gateway

| Event type | Producer | Consumers | Payload baseline |
|------------|----------|-----------|------------------|
| `payout.queued` | HITL Payout Gateway | Notification, Activity Command Center | `payout_id`, `period`, `veto_until`, `requires_2fa` |
| `payout.vetoed` | HITL Payout Gateway | Notification, Ledger, Blockchain Auditor | `payout_id`, `decision_id`, `reason_code`, `audit_hash` |
| `payout.confirmed` | HITL Payout Gateway | Wallet, Blockchain Auditor | `payout_id`, `decision_id`, `confirmed_by_role`, `audit_hash` |
| `payout.executed` | HITL Payout Gateway | Ledger, Analytics, Notification, Blockchain Auditor | `payout_id`, `execution_ref_hash`, `status`, `audit_hash` |
| `payout.failed` | HITL Payout Gateway | Notification, Activity Command Center | `payout_id`, `error_code`, `retryable` |

### Private Blockchain Auditor

| Event type | Producer | Consumers | Payload baseline |
|------------|----------|-----------|------------------|
| `audit.recorded` | Private Blockchain Auditor | Source service, Activity Command Center | `event_id`, `audit_hash`, `block_ref`, `recorded_at` |
| `audit.record_failed` | Private Blockchain Auditor | Source service, Notification | `event_id`, `audit_hash`, `error_code`, `retryable` |
| `audit.verify_completed` | Private Blockchain Auditor | Activity Command Center | `event_id`, `audit_hash`, `verified`, `block_ref` |

### Policy, Notification и AI

| Event type | Producer | Consumers | Payload baseline |
|------------|----------|-----------|------------------|
| `policy.updated` | Policy Manager | Все сервисы, Activity Command Center, Blockchain Auditor | `policy_key`, `version`, `changed_by_role`, `audit_hash` |
| `notification.requested` | Любой сервис | Notification Gateway | `notification_id`, `recipient_role`, `channel_hint`, `template_key` |
| `notification.delivered` | Notification Gateway | Source service, Analytics | `notification_id`, `channel`, `delivered_at` |
| `ai.action.proposed` | Neuro-Agent Orchestrator | Activity Command Center, Policy Manager | `action_id`, `action_type`, `risk_level`, `explanation_hash` |
| `ai.action.approved` | Activity Command Center | Neuro-Agent Orchestrator, Blockchain Auditor | `action_id`, `decision_id`, `approved_by_role`, `audit_hash` |
| `ai.action.rejected` | Activity Command Center | Neuro-Agent Orchestrator, Notification | `action_id`, `decision_id`, `reason_code` |
| `tenant.isolation_violation` | API Gateway / сервисы | Security/Audit, Activity Command Center | `resource_type`, `requested_tenant_hash`, `actor_hash`, `correlation_id` |

## 4. Доставка и ретраи

- Consumers обязаны быть идемпотентными по `event_id`.
- Shared contract для unit-тестов и первых сервисов: `IdempotentEventProcessor`
  принимает `EventEnvelope`, вызывает handler один раз для нового `event_id` и
  пропускает повторную доставку уже успешно завершённого события.
- Ошибка обработки отправляет сообщение в retry queue с экспоненциальной
  задержкой; после исчерпания ретраев событие попадает в `nmc.dlx`.
- События, влияющие на выплаты, аудит, политики и публикации, должны
  сохраняться через outbox/inbox pattern или эквивалентную транзакционную
  гарантию.
- Consumer не должен обращаться к данным другого tenant даже при ошибочном
  routing key; tenant context проверяется повторно.

## 5. Версионирование

- Добавление необязательного поля допускается в minor version.
- Удаление/переименование поля или изменение типа требует major version.
- Producer обязан публиковать старую и новую версию в течение миграционного
  окна, если есть активные consumers старой схемы.
- Schema registry или каталог AsyncAPI добавляется на этапе реализации
  RabbitMQ-инфраструктуры.
