#!/bin/bash
# Phase 5.1: Automated PostgreSQL backup with WAL archiving
# Usage: ./scripts/backup_postgres.sh [full|wal|restore]

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/tmp/pg_backups}"
DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
S3_BUCKET="${S3_BUCKET:-}"

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }

do_full_backup() {
    local stamp; stamp=$(date +'%Y%m%d_%H%M%S')
    local dump_file="${BACKUP_DIR}/pg_full_${stamp}.dump"

    log "Starting full backup → $dump_file"
    PGPASSWORD="${DB_PASSWORD}" pg_dump \
        -h "$DB_HOST" \
        -p "$DB_PORT" \
        -U "$DB_USER" \
        -d "$DB_NAME" \
        -F c \
        -Z 9 \
        --verbose \
        --file="$dump_file"

    gzip -f "$dump_file"
    log "Full backup complete: ${dump_file}.gz ($(du -h "${dump_file}.gz" | cut -f1))"

    # Загрузка в S3 если настроен
    if [[ -n "$S3_BUCKET" ]]; then
        aws s3 cp "${dump_file}.gz" "${S3_BUCKET}/postgres/${stamp}/"
    fi

    # Очистка старых бэкапов
    find "$BACKUP_DIR" -name 'pg_full_*.dump.gz' -mtime +"$RETENTION_DAYS" -delete
}

do_wal_archive() {
    local wal_path="$1"
    local wal_file; wal_file=$(basename "$wal_path")
    local archive_dir="${BACKUP_DIR}/wal"

    mkdir -p "$archive_dir"
    cp "$wal_path" "${archive_dir}/${wal_file}"

    if [[ -n "$S3_BUCKET" ]]; then
        aws s3 cp "$wal_path" "${S3_BUCKET}/postgres/wal/${wal_file}"
    fi
}

do_restore() {
    local dump_file="${1:-}"
    if [[ -z "$dump_file" ]]; then
        dump_file=$(ls -t "$BACKUP_DIR"/pg_full_*.dump.gz 2>/dev/null | head -1)
    fi

    if [[ -z "$dump_file" ]]; then
        log "ERROR: no backup found in $BACKUP_DIR"
        exit 1
    fi

    log "Restoring from $dump_file"
    gunzip -c "$dump_file" | PGPASSWORD="${DB_PASSWORD}" pg_restore \
        -h "$DB_HOST" \
        -p "$DB_PORT" \
        -U "$DB_USER" \
        -d "$DB_NAME" \
        --clean \
        --if-exists \
        --verbose
    log "Restore complete"
}

do_analyze() {
    log "Running ANALYZE for query planner"
    PGPASSWORD="${DB_PASSWORD}" psql \
        -h "$DB_HOST" \
        -p "$DB_PORT" \
        -U "$DB_USER" \
        -d "$DB_NAME" \
        -c "ANALYZE VERBOSE"
    log "ANALYZE complete"
}

case "${1:-full}" in
    full)    do_full_backup ;;
    wal)     do_wal_archive "$2" ;;
    restore) do_restore "$2" ;;
    analyze) do_analyze ;;
    *)
        echo "Usage: $0 [full|wal <path>|restore [file]|analyze]"
        exit 1
        ;;
esac
