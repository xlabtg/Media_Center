# Минимальные production-ресурсы НМЦ

Дата фиксации: 2026-06-22.

Статус: capacity-ready для issue #211.

Документ фиксирует профиль `nmc-minimal-100upm`: минималистичный production
для одного tenant на раннем этапе, где ожидается до 100 пользователей в минуту.
Цель профиля - запустить только `recommended-core` сервисы, дать каждому
сервису комфортный минимум CPU/RAM и не включать тяжелые или экспериментальные
контуры до отдельного go/no-go.

Контракт проверяется тестом
`tests/test_minimal_production_resources_issue211_contract.py`.

## 1. Граница профиля

Профиль предназначен для small production/pilot, а не для полноценного
мультизонального HA. Маркер риска: `not_full_ha`. Такой запуск допустим только
при включенных backup, alerting, tenant isolation, SRE runbook и evidence
policy `no_pdn_no_secrets`.

Нагрузочная модель:

- входящий поток: до 100 пользователей в минуту;
- расчетный API-envelope: до 500 API requests/minute на tenant
  (примерно 8-10 rps steady-state) с кратким burst до 60 запросов;
- публикации: до 200 publication jobs/minute как целевой профиль из
  [LOAD_TESTING.md](LOAD_TESTING.md);
- CGLR и Contribution Ledger сохраняют запас под stage-6 technical KPI
  (`100 req/s` и `50 событий/с`) за счет очередей, idempotency и локального
  scaling trigger;
- реальные ПДн, секреты, платежные реквизиты, суммы выплат и raw content не
  попадают в логи, evidence, backup manifest или audit-chain payload.

## 2. Что запускать

`recommended-core` включает только сервисы, необходимые для раннего production:

- API Gateway как единая внешняя точка входа, tenant-aware auth и лимиты;
- Web Cabinet и Activity Command Center для пользователей, tenant-admin и
  Совета;
- Contribution Ledger, CGLR, Messenger Adapter, HITL Payout Gateway и
  Blockchain Auditor как пять ключевых бизнес-контуров;
- Wallet, Notification Gateway и Policy Manager как поддерживающие сервисы
  для выплат, уведомлений, veto/approval и policy thresholds;
- Analytics Engine в легком режиме scheduled KPI без автономного RL-action.

Не запускать в этом профиле без отдельного решения Совета/SRE:

- Neuro-Agent Orchestrator и любые autonomous agent workers;
- Voice-to-Chain с локальным whisper.cpp;
- массовый Playwright/proxy crawling;
- self-service Tenant Marketplace для публичного onboarding;
- multi-tenant scale-out за пределы одного production tenant;
- отдельные read replicas, multi-AZ failover и тяжелые data lake jobs.

## 3. Память одного сервиса

Комфортный минимум одного FastAPI-сервиса:

- легкий gateway/control-plane сервис: 256 MiB request, 512 MiB limit;
- бизнес-сервис с очередями, idempotency и audit events: 512 MiB request,
  1 GiB limit;
- CGLR с шаблонами, link rotation и ChromaDB client: 768 MiB request,
  1.5 GiB limit;
- лимит должен быть примерно в 2 раза выше request, чтобы выдержать cold start,
  pydantic/model cache, burst и краткую деградацию внешних API.

## 4. Бюджет сервисов

| Сервис | Реплики | CPU request | CPU limit | RAM request | RAM limit |
|--------|---------|-------------|-----------|-------------|-----------|
| API Gateway | 2 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |
| Web Cabinet | 1 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |
| Activity Command Center | 1 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |
| Contribution Ledger | 1 | 0.5 vCPU | 1 vCPU | 512 MiB | 1 GiB |
| CGLR | 1 | 0.5 vCPU | 1 vCPU | 768 MiB | 1.5 GiB |
| Messenger Adapter | 1 | 0.5 vCPU | 1 vCPU | 512 MiB | 1 GiB |
| HITL Payout Gateway | 1 | 0.5 vCPU | 1 vCPU | 512 MiB | 1 GiB |
| Wallet | 1 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |
| Notification Gateway | 1 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |
| Policy Manager | 1 | 0.25 vCPU | 0.5 vCPU | 256 MiB | 512 MiB |
| Blockchain Auditor | 1 | 0.5 vCPU | 1 vCPU | 512 MiB | 1 GiB |
| Analytics Engine | 1 | 0.5 vCPU | 1 vCPU | 512 MiB | 1 GiB |

App subtotal: 4.75 vCPU requests, 9.5 vCPU limits, 5 GiB RAM requests, 10 GiB RAM limits.

## 5. Бюджет инфраструктуры

| Компонент | CPU request | RAM request | Диск | Примечание |
|-----------|-------------|-------------|------|------------|
| PostgreSQL 17 | 2 vCPU | 4 GiB | 60 GiB NVMe | Основная transactional БД, WAL и backup window |
| Redis 7.4 | 0.5 vCPU | 1 GiB | 5 GiB | Cache, idempotency, veto/session counters |
| RabbitMQ 4.1 | 1 vCPU | 2 GiB | 10 GiB | Domain events, publication and HITL queues |
| ChromaDB 1.5.9 | 1 vCPU | 2 GiB | 20 GiB | Tenant-scoped vector memory, без heavy RAG batch |
| MinIO / S3 | 1 vCPU | 2 GiB | 100 GiB external | Object storage; production лучше вынести во внешний S3 |
| Prometheus + Alertmanager + Grafana + OpenTelemetry Collector | 1.5 vCPU | 4 GiB | 40 GiB | Метрики 7-15 дней, alerts и lightweight traces |
| Besu QBFT audit-chain | 3 vCPU | 6 GiB | 80 GiB | 4 lightweight validator nodes для hash-only audit |

Инфраструктурный subtotal: около 10.5 vCPU requests, 21 GiB RAM requests,
215 GiB локального диска и 100 GiB external S3/object storage с запасом на WAL,
queues, retention и chain data. Если MinIO хранит object storage локально, а
не во внешнем S3, NVMe floor нужно поднять до 400 GiB.

External backup/object storage: 100 GiB на первый production tenant, отдельно
от локального NVMe. Retention, restore drill и evidence policy задаются в
[DISASTER_RECOVERY.md](DISASTER_RECOVERY.md).

## 6. Минимальная машина

Recommended floor: 16 vCPU, 32 GiB RAM, 300 GiB NVMe.

Этот floor покрывает app requests, инфраструктурные requests, external S3 для
object storage и оставляет примерно 20 % RAM-headroom для kernel page cache,
rolling restart, Prometheus retention, RabbitMQ bursts и краткого роста
очередей. Если MinIO/S3 или Besu вынесены в managed/external окружение,
допустим floor 8 vCPU, 16 GiB RAM, 160 GiB NVMe для app + PostgreSQL + Redis +
RabbitMQ + observability, но это уже другой deployment decision и его нужно
зафиксировать в PR/ops evidence.

Минимальная production-схема:

- 1 compute node или small single-zone cluster с ресурсами не ниже floor;
- отдельный external backup bucket;
- `make up`/docker-compose допустим только как bootstrap reference, не как
  единственный production runtime без секретов, TLS и backup automation;
- обязательны Prometheus, Alertmanager, SRE runbook и restore drill до приема
  реальных пользователей.

## 7. Лимиты tenant

Для `nmc-minimal-100upm` tenant resource plan должен начинаться с таких
значений:

| Лимит | Значение | Причина |
|-------|----------|---------|
| API window | 600 requests/minute | 100 users/minute x до 5 запросов + headroom |
| API burst | 60 requests | Краткий входной всплеск без отказа всей системы |
| Concurrent API requests | 50 | Защита Postgres/RabbitMQ от fan-out |
| Publication queue depth | 1000 jobs | До 5 минут запаса при 200 jobs/minute |
| HITL queue depth | 200 operations | Ручной veto/approval не должен падать от burst |
| Object storage soft quota | 100 GiB | Первый tenant без raw media hoarding |
| Vector collections | tenant/domain scoped | Изоляция ChromaDB и предсказуемый memory footprint |

Gateway должен отклонять превышения tenant-local лимитов через
`TenantResourcePlan`, не влияя на другие tenant'ы.

## 8. Когда масштабировать

Горизонтально масштабировать сервис или увеличивать quota нужно, если один из
сигналов держится дольше 15 минут:

- API Gateway p95 выше 250 мс или CPU выше 60 %;
- CGLR p95 выше 200 мс на steady-state, рост template/cache misses или backlog;
- Messenger queue oldest job старше 60 секунд при здоровых внешних API;
- HITL queue/veto p95 выше 5 секунд;
- PostgreSQL CPU выше 60 %, connection pool выше 70 % или WAL растет быстрее
  backup window;
- RabbitMQ memory watermark выше 70 % или publication backlog растет быстрее,
  чем consumer успевает drain;
- Prometheus retention падает ниже 7 дней из-за диска.

Перед повышением лимитов нужно приложить нагрузочный лог или SRE evidence без
ПДн/секретов и обновить этот документ, если меняется baseline.
