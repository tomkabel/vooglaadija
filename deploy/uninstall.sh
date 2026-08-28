#!/usr/bin/env bash
# ============================================
# Vooglaadija — Uninstall helper
# ============================================
# Removes the Vooglaadija application from the local Docker Compose stack
# (dev/test/production standalone). Data volumes are preserved by default;
# pass --purge to delete them too.
#
# Usage:
#   ./deploy/uninstall.sh                # remove local compose stack (volumes kept)
#   ./deploy/uninstall.sh --purge        # remove stack AND data volumes
#   ./deploy/uninstall.sh --help
# ============================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

die() { log_error "$1"; exit 1; }

confirm() {
  local response
  read -r -p "$1 [y/N]: " response
  [[ "$response" =~ ^[Yy]$ ]]
}

remove_local_stack() {
  local purge="${1:-false}"
  if $purge; then
    log_warn "Stopping the stack and DELETING all data volumes (postgres, redis, storage)..."
    confirm "This is IRREVERSIBLE. Delete all Vooglaadija data?" || { log_info "Aborted."; exit 0; }
    docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml down -v --remove-orphans \
      || die "Failed to stop the stack."
    log_info "Done. Stack removed and data volumes deleted."
  else
    log_info "Stopping the compose stack (volumes are preserved)..."
    docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml down --remove-orphans \
      || die "Failed to stop the stack."
    log_info "Done. Data volumes (ytprocessor-postgres-data, ytprocessor-redis-data, ytprocessor-storage) were kept."
    log_warn "To also delete all data: ./deploy/uninstall.sh --purge"
  fi
}

case "${1:-}" in
  --purge)
    remove_local_stack true
    ;;
  --help|-h)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  "")
    if confirm "Remove the local Docker Compose stack (data preserved)?"; then
      remove_local_stack false
    fi
    ;;
  *)
    die "Usage: $0 [--purge|--help]"
    ;;
esac
