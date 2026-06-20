# Private Blockchain Auditor

**Статус:** 🟢 реализовано · **Этап:** Этап 2 — Ключевые микросервисы · **Компонент:** `component:blockchain-auditor`

Неизменяемый аудит ключевых событий в приватной блокчейн-сети: только SHA256-хэши и метаданные, доступ только для Совета.

## Зона ответственности
- Подключение к приватной сети Hyperledger Besu 26.6.1 (QBFT) через внутренний gRPC connector
- Детерминированная генерация SHA256-хэшей событий
- Пакетная запись (batch) хэшей для эффективности
- Контроль доступа (только Совет) и верификация записей

## Основные интерфейсы
- **POST** `/audit/record` — принять batch hash-only audit records и записать их в private chain
- **GET** `/audit/records/{event_id}` — получить записанный audit record по `event_id`
- **POST** `/audit/verify` — пересчитать hash события и сравнить с записанным audit record
- **GET** `/audit/verify?event_id=&hash=` — проверить записанный hash по `event_id`

## Реализовано в issue #49
- `hash_generator` формирует детерминированный SHA256 по canonical JSON
  (`sort_keys=True`) и возвращает canonical payload для проверки.
- `GrpcBlockchainAuditConnector` использует `BLOCKCHAIN_AUDITOR_URL` и gRPC
  transport protocol для записи/чтения hash-only audit records.
- До generated proto/stub используется `InMemoryGrpcBlockchainAuditTransport`
  в unit-тестах, чтобы зафиксировать контракт записи и чтения.
- Metadata перед записью проверяются на отсутствие ПДн, сумм, токенов, сырого
  контента, голоса и transcript.

## Реализовано в issue #50
- реализованы access_controller и batch_writer для council-only доступа и
  пакетной записи.
- `BlockchainAuditAccessController` применяет deny-by-default RBAC: чтение,
  одиночная запись и batch-запись audit records доступны только роли Совета,
  то есть роль `council`.
- Access controller проверяет соответствие `tenant_id` команды и доверенного
  `TenantContext`; cross-tenant запись или чтение отклоняется до transport.
- `GrpcBlockchainAuditConnector.record_audit_hashes()` передаёт пачку хэшей в
  transport одним batch-вызовом после проверки metadata policy.
- `AuditBatchWriter` проверяет размер набора и использует batch API одним
  transport-вызовом без одиночных сетевых вызовов для каждого audit record.

## Реализовано в issue #51
- API верификации audit records доступен через `POST /audit/verify` и
  совместимый `GET /audit/verify?event_id=&hash=`.
- `create_blockchain_auditor_app()` собирает FastAPI-приложение через общий
  service template и документирует `/audit/verify` в OpenAPI.
- `POST /audit/verify` принимает `event_id`, `event_type`, `timestamp`,
  `points` и безопасные metadata, пересчитывает deterministic SHA256 через
  canonical JSON и возвращает `matched`, `recorded_hash`, `calculated_hash`,
  `block_ref` и `mismatch_reason`.
- `GET /audit/verify?event_id=&hash=` сохраняет совместимый read-only контракт
  проверки уже рассчитанного hash.
- Отсутствующая запись возвращает error envelope `audit_record_not_found`;
  расхождение hash возвращается как `matched=false` без ошибки транспорта.

## Реализовано в issue #52
- `POST /audit/record` принимает `records[]`, берёт `tenant_id` только из
  проверенного `TenantContext`, пишет batch через `AuditBatchWriter` и
  возвращает список receipt records с `block_ref`.
- `GET /audit/records/{event_id}` читает hash-only audit record через тот же
  council-only access controller, что и verify API.
- Сквозной acceptance-контракт фиксирует, что в private chain попадают только
  SHA256-хэши и безопасные metadata, sensitive keys (`amount`, ПДн, токены,
  raw content) отклоняются до transport, batch-запись использует один
  transport-вызов, а записанная запись верифицируется через `/audit/verify`.

## Реализовано в issue #79
- `infra/blockchain` добавляет optional compose-профиль Hyperledger Besu
  26.6.1/QBFT с четырьмя validator-нодами, bootstrap генерацией `genesis.json`,
  `static-nodes.json`, node keys и `permissions_config.toml` без коммита
  приватных ключей.
- Внутренний endpoint для auditor зафиксирован как
  `BLOCKCHAIN_AUDITOR_URL=grpc://besu-auditor.internal:50051`; низкоуровневый
  Besu RPC alias `besu-rpc` остаётся внутри docker-compose сети.
- RPC/P2P порты Besu не публикуются на host, peer-доступ ограничен
  `nodes-allowlist`, а операции audit records по-прежнему проходят через
  council-only RBAC сервиса `blockchain-auditor`.
- `infra/observability/prometheus/prometheus.blockchain.yml` и rules
  `blockchain-auditor.yml` добавляют monitoring job `private-blockchain-besu`
  и alerts на недоступность нод или риск потери QBFT-кворума.
- Runbook описывает snapshot/restore Docker volume с genesis, permissioning и
  node keys как секретного операционного артефакта вне репозитория.

## Модель данных (черновик)
- **audit_records** — `tenant_id`, `event_type`, `hash`, `metadata`, `block_ref`, `created_at`

## Зависимости
- Приватная блокчейн-сеть Hyperledger Besu 26.6.1/QBFT (`BLOCKCHAIN_AUDITOR_URL`), gRPC connector
- RBAC (роль Совета)

## Безопасность и мультитенантность
- В сеть пишутся **только** SHA256-хэши и метаданные — без сумм и ПДн
- Чтение, одиночная запись, REST batch-запись и верификация аудита доступны
  только роли Совета
- Все операции сравнивают `tenant_id` ресурса с проверенным `TenantContext`
- Хэш детерминирован (`sort_keys=True`) и верифицируем
- API верификации доступен только роли Совета и не пишет исходный event payload
  в audit-chain.

## Связанные задачи (issue)
- [#49](https://github.com/xlabtg/Media_Center/issues/49) — Коннектор сети (Hyperledger Besu/QBFT, gRPC) + hash_generator (`type:feature`)
- [#50](https://github.com/xlabtg/Media_Center/issues/50) — access_controller (только Совет) + batch_writer (`type:feature`)
- [#51](https://github.com/xlabtg/Media_Center/issues/51) — API верификации записей + тесты (`type:feature`)
- [#52](https://github.com/xlabtg/Media_Center/issues/52) — 🔗 Private Blockchain Auditor (`type:epic`)
- [#79](https://github.com/xlabtg/Media_Center/issues/79) — Развёртывание приватной блокчейн-сети (`type:feature`)

## Связанные документы
- [SECURITY.md](../SECURITY.md)
- [GOVERNANCE.md](../GOVERNANCE.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [infra/blockchain](../../infra/blockchain)
- [ADR-0006](../adr/0006-technology-stack-and-versions.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Спецификация синхронизирована с реализацией Private Blockchain Auditor для issue #52.</sub>
