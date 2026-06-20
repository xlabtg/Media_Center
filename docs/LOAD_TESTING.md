# Нагрузочное тестирование

Статус: baseline для issue #85, этап 6 — QA, безопасность, нагрузка.

Документ фиксирует воспроизводимые сценарии нагрузки для целевых технических
KPI пилота. Быстрый CI-контракт запускает in-memory контуры сервисов без
внешней сети, реальных платёжных шлюзов и реальных площадок. Полный прогон с
PostgreSQL, Redis, RabbitMQ и внешними rate limits остаётся release/pilot gate.

## Цели #85

| Контур | Целевой показатель | CI-сценарий |
|--------|--------------------|-------------|
| CGLR | CGLR: 100 req/s при p95 < 200 мс | `cglr.generate_content` создаёт контент через FastAPI `POST /generate`, проверяет шаблон, ротацию ссылок и лог вклада. |
| Contribution Ledger | Contribution Ledger: 50 событий/с | `contribution_ledger.record_event` пишет события вклада через `POST /contributions` с уникальными idempotency keys. |
| Messenger | Messenger: 200 публикаций/мин при > 99 % успеха | `messenger.publish` публикует 100 сообщений через `UnifiedMessengerAdapter` и in-memory connector. |
| HITL | HITL: 10 очередей/ч, veto p95 < 5 с | `hitl.queue_and_veto` ставит выплаты в очередь и накладывает veto в открытом окне. |

## Как запустить

Быстрый контракт issue #85:

```bash
pytest tests/test_load_testing_issue85_acceptance_contract.py
```

Тот же сценарий как воспроизводимый experiment:

```bash
python experiments/validate_issue85_load_targets.py
```

Весь локальный CI перед PR:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```

## Что измеряется

Общий harness `libs.shared.load_testing` фиксирует:

- количество операций;
- режим запуска: последовательный, async ASGI через
  `run_async_load_scenario` или параллельный через `run_threaded_load_scenario`
  для throughput-профилей;
- прогрев CGLR до измеряемого окна, чтобы стартовая компиляция шаблона не
  смешивалась с steady-state throughput;
- successful/failed операции;
- throughput в операциях в секунду;
- p95 latency и max latency;
- success ratio;
- невыполненные условия цели.

Тест `tests/test_load_testing_issue85_acceptance_contract.py` строит
`LoadTestReport` и падает с кратким summary, если один из целевых показателей
не достигнут.

## Узкие места

Текущий CI baseline проверяет производительность in-memory бизнес-контуров и
идемпотентных API без инфраструктурного шума. По итогам stage-6 анализа
задокументированы узкие места, которые должен покрыть следующий нагрузочный
слой перед пилотом:

- Выявлено в #85: запуск `TestClient` без контекстного менеджера добавлял
  portal overhead на каждый запрос и искажал CGLR throughput. Сценарий
  `cglr.generate_content` переведён на in-process ASGI transport с ограниченным
  concurrency.
- Выявлено и устранено в #85: CGLR повторно проверял AST и компилировал
  одинаковый Jinja-шаблон на каждый `/generate`. `TemplateEngine` кеширует
  безопасно скомпилированные шаблоны через LRU-словарь.
- CGLR: рост p95 возможен на тяжёлых шаблонах, большом числе L3-кандидатов и
  при синхронной записи событий в broker.
- Contribution Ledger: основная зона риска — индексы `tenant_id + period`,
  дедупликация idempotency keys и invalidation snapshot'ов весов при высокой
  частоте событий.
- Messenger: внешний лимит площадок и retry/backoff определяют реальную
  пропускную способность; CI использует fake connector, а pilot gate должен
  отдельно проверять per-platform rate limits.
- HITL: очередь выплат и veto быстры в in-memory слое, но production-прогон
  должен измерять задержку при Redis/RabbitMQ, уведомлениях Совета и 2FA.

Если любой из этих пунктов проявится как фактический bottleneck в
инфраструктурном прогоне, PR должен приложить лог нагрузки, конкретную метрику
p95/throughput и изменение конфигурации или кода, устраняющее деградацию.
