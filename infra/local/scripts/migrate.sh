#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE_FILE="${LOCAL_COMPOSE_FILE:-infra/local/docker-compose.yml}"
ENV_FILE="${LOCAL_ENV_FILE:-infra/local/.env.local.example}"
PROJECT_NAME="${LOCAL_PROJECT_NAME:-media-center-local}"

resolve_path() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s\n' "$ROOT_DIR/$path"
  fi
}

env_value() {
  local key="$1"
  local path="$2"
  awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "$path"
}

compose() {
  docker compose \
    --project-name "$PROJECT_NAME" \
    --env-file "$ENV_FILE_PATH" \
    -f "$COMPOSE_FILE_PATH" \
    "$@"
}

wait_for_postgres() {
  local attempt
  for attempt in {1..60}; do
    if compose exec -T postgres pg_isready \
      -U "$POSTGRES_USER" \
      -d "$POSTGRES_DB" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "PostgreSQL is not ready after 120 seconds" >&2
  return 1
}

COMPOSE_FILE_PATH="$(resolve_path "$COMPOSE_FILE")"
ENV_FILE_PATH="$(resolve_path "$ENV_FILE")"

if [[ ! -f "$COMPOSE_FILE_PATH" ]]; then
  echo "Missing compose file: $COMPOSE_FILE_PATH" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE_PATH" ]]; then
  echo "Missing env file: $ENV_FILE_PATH" >&2
  exit 1
fi

POSTGRES_DB="${POSTGRES_DB:-$(env_value POSTGRES_DB "$ENV_FILE_PATH")}"
POSTGRES_USER="${POSTGRES_USER:-$(env_value POSTGRES_USER "$ENV_FILE_PATH")}"

wait_for_postgres

compose exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -f /migrations/001_dev_schema.sql

LOCAL_COMPOSE_FILE="$COMPOSE_FILE" \
LOCAL_ENV_FILE="$ENV_FILE" \
LOCAL_PROJECT_NAME="$PROJECT_NAME" \
bash "$ROOT_DIR/infra/local/scripts/seed.sh"
