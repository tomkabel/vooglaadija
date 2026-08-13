#!/usr/bin/env bash
# ============================================
# Vooglaadija — Uninstall helper
# ============================================
# Removes the Vooglaadija application from a local Docker Compose stack
# (dev/test) and provides guided cleanup for a Coolify-managed deployment.
#
# Usage:
#   ./deploy/uninstall.sh                          # remove local compose stack
#   APP_UUID=<uuid> COOLIFY_API_TOKEN=<token> ./deploy/uninstall.sh --coolify
# ============================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

confirm() {
  local response
  read -r -p "$1 [y/N]: " response
  [[ "$response" =~ ^[Yy]$ ]]
}

remove_local_stack() {
  log_info "Stopping local compose stack (volumes are preserved)..."
  docker compose -f docker-compose.yml -f docker-compose.local.yml down --remove-orphans 2>/dev/null || true
  log_info "Done. Data volumes (ytprocessor-postgres-data, ytprocessor-redis-data, ytprocessor-storage) were kept."
  log_warn "To also delete all data: docker compose -f docker-compose.yml -f docker-compose.local.yml down -v"
}

remove_coolify_app() {
  local app_uuid="${APP_UUID:-}"
  local token="${COOLIFY_API_TOKEN:-}"
  [[ -n "$app_uuid" ]] || die "APP_UUID is required for --coolify mode."
  [[ -n "$token" ]] || die "COOLIFY_API_TOKEN is required for --coolify mode."
  curl -sS --max-time 30 -X DELETE \
    -H "Authorization: Bearer ${token}" \
    "http://127.0.0.1:8000/api/v1/applications/${app_uuid}" || true
  log_info "Application ${app_uuid} deleted from Coolify."
  log_info "To remove Coolify itself, use: coolify uninstall  (see Coolify docs)."
}

die() { log_error "$1"; exit 1; }

case "${1:-}" in
  --coolify)
    remove_coolify_app
    ;;
  --help|-h)
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  "")
    if confirm "Remove the local Docker Compose stack (data preserved)? This only affects a locally-run stack, not a Coolify deployment."; then
      remove_local_stack
    fi
    ;;
  *)
    die "Usage: $0 [--coolify|--help]"
    ;;
esac
