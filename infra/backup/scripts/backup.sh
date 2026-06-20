#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE_FILE="${LOCAL_COMPOSE_FILE:-infra/local/docker-compose.yml}"
ENV_FILE="${LOCAL_ENV_FILE:-infra/local/.env.local.example}"
PROJECT_NAME="${LOCAL_PROJECT_NAME:-media-center-local}"
BACKUP_ROOT="${BACKUP_ROOT:-$ROOT_DIR/.backups/local}"
DRY_RUN=0
COMPONENT="all"

usage() {
  cat <<'USAGE'
Usage: infra/backup/scripts/backup.sh [--dry-run] [all|postgres|chroma|minio]

Creates local backup artifacts for PostgreSQL, ChromaDB and S3/MinIO.
Set BACKUP_ROOT to choose the destination directory.
USAGE
}

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

print_cmd() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    print_cmd "$@"
  else
    "$@"
  fi
}

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Missing file: $path" >&2
    exit 1
  fi
}

prepare_backup_root() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    print_cmd mkdir -p \
      "$BACKUP_ROOT/postgres" \
      "$BACKUP_ROOT/chroma" \
      "$BACKUP_ROOT/minio"
  else
    mkdir -p \
      "$BACKUP_ROOT/postgres" \
      "$BACKUP_ROOT/chroma" \
      "$BACKUP_ROOT/minio"
  fi
}

backup_postgres() {
  local archive="postgres-${TIMESTAMP}.dump"

  run compose exec -T postgres \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "/tmp/$archive"
  run compose cp "postgres:/tmp/$archive" "$BACKUP_ROOT/postgres/$archive"
  run compose exec -T postgres rm -f "/tmp/$archive"
}

backup_chroma() {
  local archive="chroma-${TIMESTAMP}.tgz"

  run compose exec -T chroma \
    sh -ec "tar -czf /tmp/$archive -C /chroma/chroma ."
  run compose cp "chroma:/tmp/$archive" "$BACKUP_ROOT/chroma/$archive"
  run compose exec -T chroma rm -f "/tmp/$archive"
}

backup_minio() {
  local archive="minio-${TIMESTAMP}.tgz"

  run compose exec -T \
    -e MINIO_BUCKET="$MINIO_BUCKET" \
    -e BACKUP_ARCHIVE="$archive" \
    minio sh -ec \
    'mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && rm -rf "/tmp/minio-$MINIO_BUCKET" && mkdir -p "/tmp/minio-$MINIO_BUCKET" && mc mirror --overwrite "local/$MINIO_BUCKET" "/tmp/minio-$MINIO_BUCKET" && tar -czf "/tmp/$BACKUP_ARCHIVE" -C "/tmp" "minio-$MINIO_BUCKET"'
  run compose cp "minio:/tmp/$archive" "$BACKUP_ROOT/minio/$archive"
  run compose exec -T minio sh -ec "rm -f /tmp/$archive && rm -rf /tmp/minio-$MINIO_BUCKET"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    all|postgres|chroma|minio)
      COMPONENT="$1"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

COMPOSE_FILE_PATH="$(resolve_path "$COMPOSE_FILE")"
ENV_FILE_PATH="$(resolve_path "$ENV_FILE")"

require_file "$COMPOSE_FILE_PATH"
require_file "$ENV_FILE_PATH"

POSTGRES_DB="${POSTGRES_DB:-$(env_value POSTGRES_DB "$ENV_FILE_PATH")}"
POSTGRES_USER="${POSTGRES_USER:-$(env_value POSTGRES_USER "$ENV_FILE_PATH")}"
MINIO_BUCKET="${MINIO_BUCKET:-$(env_value MINIO_BUCKET "$ENV_FILE_PATH")}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

prepare_backup_root

case "$COMPONENT" in
  all)
    backup_postgres
    backup_chroma
    backup_minio
    ;;
  postgres)
    backup_postgres
    ;;
  chroma)
    backup_chroma
    ;;
  minio)
    backup_minio
    ;;
esac

echo "Backup plan completed for component=$COMPONENT backup_root=$BACKUP_ROOT"
