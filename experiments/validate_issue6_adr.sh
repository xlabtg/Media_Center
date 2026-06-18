#!/usr/bin/env bash
set -euo pipefail

required_files=(
  "README.md"
  "CONTRIBUTING.md"
  "docs/ARCHITECTURE.md"
  "docs/adr/README.md"
  "docs/adr/0006-technology-stack-and-versions.md"
  "docs/modules/blockchain-auditor.md"
)

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "Нет обязательного файла: $file" >&2
    exit 1
  fi
done

adr_patterns=(
  "ADR-0006: Технологический стек и версии"
  "**Статус:** Accepted"
  "Python 3.13.x"
  "python:3.13.14-slim"
  "FastAPI | \`0.137.2\`"
  "Pydantic | \`2.13.4\`"
  "SQLAlchemy | \`2.0.51\`"
  "PostgreSQL | \`postgres:17\`"
  "Redis | \`redis:7.4\`"
  "RabbitMQ | \`rabbitmq:4.1-management\`"
  "ChromaDB | \`chromadb/chroma:1.5.9\`"
  "MinIO | \`minio/minio:RELEASE.2025-09-07T16-13-09Z\`"
  "Hyperledger Besu | \`hyperledger/besu:26.6.1\`"
  "QBFT"
  "whisper.cpp v1.9.0"
)

for pattern in "${adr_patterns[@]}"; do
  if ! rg -F -q "$pattern" docs/adr/0006-technology-stack-and-versions.md; then
    echo "В ADR-0006 не найден маркер: $pattern" >&2
    exit 1
  fi
done

index_patterns=(
  "ADR-0006"
  "Технологический стек и версии"
)

for pattern in "${index_patterns[@]}"; do
  if ! rg -F -q "$pattern" docs/adr/README.md; then
    echo "В индексе ADR не найден маркер: $pattern" >&2
    exit 1
  fi
done

baseline_patterns=(
  "Python 3.13.x"
  "FastAPI 0.137.2"
  "Hyperledger Besu 26.6.1"
  "ADR-0006"
)

for pattern in "${baseline_patterns[@]}"; do
  if ! rg -F -q "$pattern" README.md docs/ARCHITECTURE.md; then
    echo "В README.md/docs/ARCHITECTURE.md не найден маркер: $pattern" >&2
    exit 1
  fi
done

obsolete_patterns=(
  "Besu/Quorum"
  "Besu / Quorum"
  "приватный шард TON"
  "будет уточняться.*#6"
  "версии.*будут уточняться"
)

for pattern in "${obsolete_patterns[@]}"; do
  if rg -n "$pattern" README.md docs/ARCHITECTURE.md docs/modules/blockchain-auditor.md docs/DEVELOPMENT_PLAN.md; then
    echo "Найдена устаревшая формулировка: $pattern" >&2
    exit 1
  fi
done

echo "Issue #6 ADR validation passed."
