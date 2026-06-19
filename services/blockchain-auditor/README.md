# Private Blockchain Auditor

**Статус:** реализованы базовый коннектор, access controller и batch writer
для issue #49/#50.

## Назначение

Private Blockchain Auditor записывает и проверяет SHA256-хэши операций в
приватной permissioned audit-chain. Сервис является единственной границей
интеграции доменных сервисов с блокчейн-сетью.

## Границы ответственности

- принимает только hash payload и технические метаданные;
- не принимает ПДн, суммы выплат, токены площадок или сырой контент;
- управляет пакетной записью и проверкой audit records;
- ограничивает доступ к операциям аудита по ролям Совета.

## Реализованный baseline

- `blockchain_auditor.hash_generator.generate_event_hash()` формирует
  детерминированный `SHA256` по canonical JSON с `sort_keys=True`.
- `blockchain_auditor.settings.build_blockchain_auditor_settings()` читает
  `BLOCKCHAIN_AUDITOR_URL` из окружения и принимает только `grpc://` или
  `grpcs://` endpoint.
- `blockchain_auditor.connector.GrpcBlockchainAuditConnector` пишет и читает
  hash-only audit records через gRPC transport protocol.
- `InMemoryGrpcBlockchainAuditTransport` используется в unit-тестах как
  проверяемая замена generated gRPC stub до появления proto-интеграции.
- Metadata перед записью проверяются на отсутствие сумм, ПДн, токенов,
  сырого контента, голоса и transcript.
- `blockchain_auditor.access_controller.BlockchainAuditAccessController`
  требует роль `council` и проверяет соответствие `tenant_id` доверенному
  `TenantContext` перед записью, batch-записью и чтением.
- `blockchain_auditor.batch_writer.AuditBatchWriter` отправляет пачки через
  `GrpcBlockchainAuditConnector.record_audit_hashes()` и не делает одиночный
  transport-вызов на каждую запись внутри batch.

## Проверки

```bash
pytest tests/test_blockchain_auditor_connector.py
```

## Связанные документы

- [Спецификация модуля](../../docs/modules/blockchain-auditor.md)
- [ADR-0004: private blockchain audit](../../docs/adr/0004-private-blockchain-audit.md)
- [ADR-0006: технологический стек](../../docs/adr/0006-technology-stack-and-versions.md)
