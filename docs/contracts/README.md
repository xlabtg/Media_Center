# Контракты межсервисного взаимодействия

Каталог фиксирует baseline контрактов для issue
[#5](https://github.com/xlabtg/Media_Center/issues/5): синхронные REST/gRPC
команды и асинхронные события RabbitMQ. Детальная OpenAPI/AsyncAPI-спецификация
будет добавляться при реализации сервисов, но границы и инварианты уже
зафиксированы здесь.

## Документы

| Документ | Назначение |
|----------|------------|
| [sync-api.md](sync-api.md) | REST/gRPC правила, API Gateway, общие headers, ошибки и карта синхронных endpoints. |
| [events.md](events.md) | RabbitMQ envelope, routing keys, схема события, каталог доменных событий. |

## Общие инварианты

- `tenant_id` берётся из проверенного JWT на API Gateway и пробрасывается как
  trusted tenant context; тело запроса не переопределяет tenant.
- `correlation_id` обязателен для трассировки HTTP-запросов, gRPC-вызовов и
  RabbitMQ-событий.
- Команды с side effect используют `Idempotency-Key`, а события используют
  стабильный `event_id`.
- В контрактах нет ПДн, токенов площадок, денежных сумм и сырого контента,
  если документ явно не разрешает поле и не указывает правовое основание.
- Cross-tenant доступ всегда возвращает `403 tenant_isolation_violation` и
  создаёт аудит-событие.

## Связанные документы

- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [adr/README.md](../adr/README.md)
- [SECURITY.md](../SECURITY.md)
- [REQUIREMENTS.md](../REQUIREMENTS.md)
