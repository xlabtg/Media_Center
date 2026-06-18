#!/usr/bin/env bash
set -euo pipefail

required_files=(
  "docs/ARCHITECTURE.md"
  "docs/adr/README.md"
  "docs/contracts/README.md"
  "docs/contracts/sync-api.md"
  "docs/contracts/events.md"
)

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "Нет обязательного файла: $file" >&2
    exit 1
  fi
done

adr_count=$(find docs/adr -maxdepth 1 -type f -name '000*.md' | wc -l | tr -d ' ')
if [[ "$adr_count" -lt 5 ]]; then
  echo "Ожидалось минимум 5 ADR, найдено: $adr_count" >&2
  exit 1
fi

architecture_patterns=(
  "Контекст системы (C4"
  "Контейнеры (C4"
  "Компоненты ключевых сервисов (C4"
  "Component Level"
)

for pattern in "${architecture_patterns[@]}"; do
  if ! rg -F -q "$pattern" docs/ARCHITECTURE.md; then
    echo "В docs/ARCHITECTURE.md не найден маркер: $pattern" >&2
    exit 1
  fi
done

contract_patterns=(
  "API Gateway"
  "RabbitMQ"
  "event_id"
  "tenant_id"
  "correlation_id"
)

for pattern in "${contract_patterns[@]}"; do
  if ! rg -F -q "$pattern" docs/contracts; then
    echo "В docs/contracts не найден маркер: $pattern" >&2
    exit 1
  fi
done

echo "Issue #5 docs validation passed."
