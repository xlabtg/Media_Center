# Chaos-тестирование отказоустойчивости

Дата фиксации: 2026-06-21.

Статус: chaos-ready для issue #89.

Документ фиксирует воспроизводимый baseline для отказов зависимостей: БД,
брокера, внешних API и proxy. Быстрый CI-контракт запускает детерминированные
in-memory сценарии без реальной сети и без остановки инфраструктуры. Полный
chaos-прогон с docker-compose, сетевыми fault injection и production-like
лимитами остаётся release/pilot gate перед этапом 7.

Контракт проверяется тестом
`tests/test_chaos_resilience_issue89_contract.py`. Общий код живёт в
`libs.shared.resilience`: `DependencyResilienceGuard`, `RetryPolicy`,
`TimeoutBudget` и `CircuitBreakerPolicy`.

## 1. Критерии приемки #89

| Критерий | Как выполняется | Быстрая проверка |
|----------|-----------------|------------------|
| Система деградирует контролируемо | Для каждого отказа задан безопасный fallback: readonly/cache для PostgreSQL, durable outbox для RabbitMQ, stale cache для external API, отключение нездорового proxy route. | `controlled_degradation` в `tests/test_chaos_resilience_issue89_contract.py` |
| Ретраи и таймауты работают | `RetryPolicy` ограничивает число попыток и backoff, `TimeoutBudget` обрывает зависшие вызовы быстрее SLO операции. | сценарии `postgresql_timeout` и `dependency_timeout` |
| Восстановление подтверждено | `CircuitBreakerPolicy` открывает контур после повторных отказов, переводит его в half-open после cooldown и закрывает после успешной probe-операции. | `recovery_confirmed` в тесте circuit breaker |

## 2. Матрица chaos-сценариев

| Зависимость | Failure mode | Ожидаемый режим | Recovery probe |
|-------------|--------------|-----------------|----------------|
| PostgreSQL | `postgresql_unavailable`, `postgresql_timeout` | Сервис возвращает readonly/cache snapshot, не теряет `tenant_id`, не пишет частичные транзакции. | Успешный lightweight query закрывает circuit breaker. |
| RabbitMQ | `rabbitmq_unavailable` | Событие принимается в локальный durable outbox с idempotency key и correlation id. | Publish в broker подтверждает drain outbox. |
| external API | `dependency_timeout`, `external_api_unavailable`, rate limit | Используется stale cache или отложенная задача без раскрытия токенов и raw payload. | Успешный probe endpoint переводит интеграцию в primary mode. |
| proxy | `proxy_unavailable` | Нездоровый proxy lease не используется; публикация либо уходит в разрешённый fallback, либо помечается как отложенная. | Новый healthy lease подтверждает восстановление route. |

## 3. Общий resilience-контракт

`DependencyResilienceGuard` оборачивает вызов внешней зависимости и возвращает
`DependencyCallResult`:

- `status=succeeded` при штатном ответе;
- `status=degraded` при безопасном fallback после исчерпания retries, timeout
  или открытого circuit breaker;
- `attempts` показывает фактическое число попыток;
- `error_code` фиксирует нормализованную причину без секретов;
- `circuit_state` показывает `closed`, `open` или `half_open`;
- `recovered=true` выставляется на успешной half-open probe, закрывшей контур.

Все ошибки нормализуются через `DependencyFailure`. В chaos-контракте
используются `DependencyKind.DATABASE`, `DependencyKind.MESSAGE_BROKER`,
`DependencyKind.EXTERNAL_API` и `DependencyKind.PROXY`, чтобы отчёты и алерты
не смешивали разные классы зависимостей.

## 4. Как запускать

Быстрый контракт issue #89:

```bash
pytest tests/test_chaos_resilience_issue89_contract.py
```

Локальный CI перед PR:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```

Полный pilot gate должен запускаться отдельно от PR CI:

1. Поднять `infra/local/docker-compose.yml`.
2. Включить fault injection для PostgreSQL, RabbitMQ, external API stubs и
   proxy pool по одному отказу за раз.
3. Проверить, что SLO alerts из `docs/SRE_RUNBOOK.md` создают incident record,
   но не раскрывают ПДн, токены, суммы выплат или raw content.
4. Вернуть зависимость, дождаться recovery probe и зафиксировать
   `recovery_confirmed`.

## 5. Evidence для ревью

PR, меняющий resilience-поведение, должен включать:

- воспроизводящий тест с отказом до исправления;
- результат `pytest tests/test_chaos_resilience_issue89_contract.py`;
- краткое описание fallback-режима и ограничения retries/timeouts;
- если меняется UI или внешний процесс, ссылку на runbook или скриншот;
- для production-like chaos-прогона - лог без секретов и ПДн.
