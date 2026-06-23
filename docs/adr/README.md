# Architecture Decision Records

Журнал ADR фиксирует ключевые архитектурные решения НМЦ. Запись получает статус
`Accepted`, когда решение становится baseline для дальнейшего проектирования и
реализации. Изменение принятого решения оформляется новым ADR со ссылкой на
предыдущую запись.

## Индекс

| ADR | Решение | Статус | Дата |
|-----|---------|--------|------|
| [ADR-0001](0001-service-boundaries-and-c4-baseline.md) | Границы микросервисов и C4 baseline | Accepted | 2026-06-18 |
| [ADR-0002](0002-sync-async-integration.md) | Синхронный API Gateway и асинхронный RabbitMQ | Accepted | 2026-06-18 |
| [ADR-0003](0003-tenant-isolation-by-design.md) | Сквозная tenant-изоляция по `tenant_id` | Accepted | 2026-06-18 |
| [ADR-0004](0004-private-blockchain-audit.md) | Приватный audit-chain только для SHA256-хэшей и метаданных | Accepted | 2026-06-18 |
| [ADR-0005](0005-hitl-for-sensitive-operations.md) | HITL-контур для выплат и чувствительных действий | Accepted | 2026-06-18 |
| [ADR-0006](0006-technology-stack-and-versions.md) | Технологический стек и версии | Accepted | 2026-06-18 |
| [ADR-0007](0007-data-model-and-tenant-storage.md) | Модель данных и tenant-aware стратегия хранения | Accepted | 2026-06-18 |
| [ADR-0008](0008-container-image-size-optimization.md) | Оптимизация размера сервисных образов | Accepted | 2026-06-22 |
| [ADR-0009](0009-ghcr-image-naming.md) | Имена сервисных образов в GHCR | Accepted | 2026-06-23 |
| [ADR-0010](0010-spiffe-mtls-s2s.md) | Целевой переход S2S на SPIFFE/SPIRE и mTLS | Accepted | 2026-06-23 |

## Формат новых ADR

Новые решения добавляются как `NNNN-short-title.md` и содержат:

- статус (`Proposed`, `Accepted`, `Superseded`, `Deprecated`);
- дату принятия или предложения;
- контекст и ограничения;
- принятое решение;
- последствия и риски;
- ссылки на документы, issue и контракты.

## Связанные документы

- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [DATA_MODEL.md](../DATA_MODEL.md)
- [contracts/README.md](../contracts/README.md)
- [SECURITY.md](../SECURITY.md)
- [S2S_AUTH.md](../S2S_AUTH.md)
- [GOVERNANCE.md](../GOVERNANCE.md)
- [COMPLIANCE.md](../COMPLIANCE.md)
- [operations/image-size-budget.md](../operations/image-size-budget.md)
