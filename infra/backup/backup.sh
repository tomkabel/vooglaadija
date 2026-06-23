#!/bin/bash
# PostgreSQL backup script for ytprocessor
#
# Usage:
#   ./backup.sh                        # Uses environment variables or defaults
#   ./backup.sh /custom/output/dir     # Custom output directory

set -e

# Configuration from environment or defaults
OUTPUT_DIR="${1:-${BACKUP_OUTPUT_DIR:-/var/backup/ytprocessor}}"
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
PGPASSWORD="${PGPASSWORD:-}"
PGDATABASE="${PGDATABASE:-ytprocessor}"

# Validate PGPASSWORD is set
if [ -z "$PGPASSWORD" ]; then
    echo "ERROR: PGPASSWORD environment variable is not set"
    exit 1
fi

export PGPASSWORD

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${OUTPUT_DIR}/ytprocessor_${TIMESTAMP}.sql.gz"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting backup of ${PGDATABASE}..."

# Create output directory
mkdir -p "${OUTPUT_DIR}"

# Perform backup with compression
pg_dump -h "${PGHOST}" \
        -p "${PGPORT}" \
        -U "${PGUSER}" \
        -d "${PGDATABASE}" \
        -Fc \
        -Z 6 \
        -f "${BACKUP_FILE}"

# Get file size
SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup complete: ${BACKUP_FILE} (${SIZE})"

# Cleanup old backups (keep last 7 days by default)
RETENTION_DAYS="${RETENTION_DAYS:-7}"
find "${OUTPUT_DIR}" -name "ytprocessor_*.sql.gz" -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Old backups cleaned up (retention: ${RETENTION_DAYS} days)"
