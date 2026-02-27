#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/backups"
RETENTION_DAYS="${PG_BACKUP_RETENTION_DAYS:-7}"
INTERVAL_SEC="${PG_BACKUP_INTERVAL_SECONDS:-3600}"

mkdir -p "$BACKUP_DIR"

while true; do
  TS="$(date -u +%Y%m%dT%H%M%SZ)"
  OUT="$BACKUP_DIR/graph_storage_${TS}.sql.gz"

  export PGPASSWORD="${POSTGRES_PASSWORD}"
  pg_dump -h graph-storage-postgres -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" | gzip > "$OUT"

  find "$BACKUP_DIR" -type f -name '*.sql.gz' -mtime +"$RETENTION_DAYS" -delete
  sleep "$INTERVAL_SEC"
done
