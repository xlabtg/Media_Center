#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_file() {
  local path="$1"
  [[ -f "$path" ]] || fail "missing file: $path"
}

assert_executable() {
  local path="$1"
  [[ -x "$path" ]] || fail "file is not executable: $path"
}

assert_contains() {
  local path="$1"
  local pattern="$2"
  grep -Fq "$pattern" "$path" || fail "missing marker in $path: $pattern"
}

assert_file "Makefile"
assert_file "infra/local/docker-compose.yml"
assert_file "infra/local/.env.local.example"
assert_file "infra/local/README.md"
assert_file "infra/local/postgres/migrations/001_dev_schema.sql"
assert_file "infra/local/postgres/seeds/001_dev_seed.sql"
assert_file "infra/local/fixtures/dev-fixtures.json"
assert_file "infra/local/scripts/migrate.sh"
assert_file "infra/local/scripts/seed.sh"
assert_executable "infra/local/scripts/migrate.sh"
assert_executable "infra/local/scripts/seed.sh"

compose_markers=(
  "postgres:"
  "image: postgres:17"
  "redis:"
  "image: redis:7.4"
  "rabbitmq:"
  "image: rabbitmq:4.1-management"
  "chroma:"
  "image: chromadb/chroma:1.5.9"
  "minio:"
  "image: minio/minio:RELEASE.2025-09-07T16-13-09Z"
  "postgres-data:"
  "redis-data:"
  "rabbitmq-data:"
  "chroma-data:"
  "minio-data:"
)

for marker in "${compose_markers[@]}"; do
  assert_contains "infra/local/docker-compose.yml" "$marker"
done

if grep -Fq ":latest" "infra/local/docker-compose.yml"; then
  fail "docker-compose must not use latest tags"
fi

env_markers=(
  "POSTGRES_DB=nmc"
  "POSTGRES_USER=nmc"
  "POSTGRES_PASSWORD="
  "REDIS_PORT=6379"
  "RABBITMQ_DEFAULT_USER=nmc"
  "RABBITMQ_DEFAULT_PASS="
  "CHROMA_PORT=8001"
  "MINIO_ROOT_USER=nmc_minio"
  "MINIO_ROOT_PASSWORD="
  "MINIO_BUCKET=nmc-dev"
)

for marker in "${env_markers[@]}"; do
  assert_contains "infra/local/.env.local.example" "$marker"
done

make_markers=(
  "up:"
  "down:"
  "test:"
  "migrate:"
  "docker compose"
  "infra/local/scripts/migrate.sh"
)

for marker in "${make_markers[@]}"; do
  assert_contains "Makefile" "$marker"
done

assert_contains "infra/local/postgres/migrations/001_dev_schema.sql" "CREATE SCHEMA IF NOT EXISTS nmc_dev;"
assert_contains "infra/local/postgres/seeds/001_dev_seed.sql" "INSERT INTO nmc_dev.tenants"
assert_contains "infra/local/fixtures/dev-fixtures.json" '"contribution_events"'
assert_contains "README.md" "make up"
assert_contains "infra/README.md" "infra/local/docker-compose.yml"
assert_contains "infra/local/README.md" "make migrate"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose \
    --project-name media-center-local-contract \
    --env-file infra/local/.env.local.example \
    -f infra/local/docker-compose.yml \
    config --quiet
else
  echo "SKIP: docker compose is not available, static contract checks completed"
fi

echo "OK: local development environment contract for issue #10 is configured"
