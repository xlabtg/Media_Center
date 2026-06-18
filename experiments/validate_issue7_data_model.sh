#!/usr/bin/env bash
set -euo pipefail

required_files=(
  "README.md"
  "docs/ARCHITECTURE.md"
  "docs/SECURITY.md"
  "docs/DATA_MODEL.md"
  "docs/adr/README.md"
  "docs/adr/0007-data-model-and-tenant-storage.md"
  "docs/modules/tenant-isolation.md"
  "docs/modules/contribution-ledger.md"
)

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "Нет обязательного файла: $file" >&2
    exit 1
  fi
done

data_model_patterns=(
  "ER-модель"
  "contributions"
  "tenant_weights"
  "idx_contributions_tenant_event_created"
  "uq_tenant_weights_tenant_member_period"
  "ChromaDB"
  "S3 / MinIO"
  "tenant_isolation_violation"
  "Alembic"
  "Row Level Security"
  "outbox_events"
)

for pattern in "${data_model_patterns[@]}"; do
  if ! rg -F -q "$pattern" docs/DATA_MODEL.md; then
    echo "В docs/DATA_MODEL.md не найден маркер: $pattern" >&2
    exit 1
  fi
done

adr_patterns=(
  "ADR-0007: Модель данных и tenant-aware стратегия хранения"
  "**Статус:** Accepted"
  "PostgreSQL"
  "ChromaDB"
  "S3 / MinIO"
  "Alembic"
  "403 tenant_isolation_violation"
)

for pattern in "${adr_patterns[@]}"; do
  if ! rg -F -q "$pattern" docs/adr/0007-data-model-and-tenant-storage.md; then
    echo "В ADR-0007 не найден маркер: $pattern" >&2
    exit 1
  fi
done

link_patterns=(
  "DATA_MODEL.md"
  "ADR-0007"
)

for pattern in "${link_patterns[@]}"; do
  if ! rg -F -q "$pattern" README.md docs/ARCHITECTURE.md docs/SECURITY.md docs/adr/README.md; then
    echo "В навигации/связанных документах не найден маркер: $pattern" >&2
    exit 1
  fi
done

echo "Issue #7 data model validation passed."
