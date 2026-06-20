#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
EVIDENCE_DIR="${RESTORE_DRILL_EVIDENCE_DIR:-$ROOT_DIR/.backups/restore-drills}"
DRY_RUN=0
RESTORE_DRILL_ID="${RESTORE_DRILL_ID:-drill-issue-99-2026-06-20}"
rto_target_minutes="${RTO_TARGET_MINUTES:-240}"
rpo_target_minutes="${RPO_TARGET_MINUTES:-60}"

usage() {
  cat <<'USAGE'
Usage: infra/backup/scripts/restore_drill.sh [--dry-run]

Records the full-stack restore_drill checklist for PostgreSQL, ChromaDB and
S3/MinIO. A destructive restore must run only in an isolated sandbox.
USAGE
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
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  cat <<DRYRUN
restore_drill=$RESTORE_DRILL_ID
rto_target_minutes=$rto_target_minutes
rpo_target_minutes=$rpo_target_minutes
component=PostgreSQL action=restore latest pg_dump into isolated sandbox
component=ChromaDB action=restore vector snapshot into isolated sandbox
component=S3/MinIO action=restore bucket mirror into isolated sandbox
check=checksum_verification
check=tenant_restore_integrity
check=cross_tenant_access_denied
check=service_health_after_restore
DRYRUN
  exit 0
fi

if [[ "${RESTORE_DRILL_CONFIRM:-}" != "sandbox" ]]; then
  echo "Set RESTORE_DRILL_CONFIRM=sandbox after preparing an isolated sandbox." >&2
  exit 1
fi

mkdir -p "$EVIDENCE_DIR"
EVIDENCE_PATH="$EVIDENCE_DIR/$RESTORE_DRILL_ID.json"

cat > "$EVIDENCE_PATH" <<JSON
{
  "restore_drill": "$RESTORE_DRILL_ID",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "rto_target_minutes": $rto_target_minutes,
  "rpo_target_minutes": $rpo_target_minutes,
  "components": ["PostgreSQL", "ChromaDB", "S3/MinIO"],
  "checks": [
    "checksum_verification",
    "tenant_restore_integrity",
    "cross_tenant_access_denied",
    "service_health_after_restore"
  ],
  "secret_policy": "no_pdn_no_secrets"
}
JSON

echo "Restore drill evidence written to $EVIDENCE_PATH"
