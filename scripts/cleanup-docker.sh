#!/bin/bash
# ============================================
# Docker Cleanup Script for Vooglaadija
# ============================================
# Frees up disk space by removing unused Docker resources.
# Safe to run between demo sessions.
#
# Usage:
#   ./scripts/cleanup-docker.sh [--all] [--images] [--volumes] [--build-cache] [--networks]
#
# Options:
#   --all          Clean everything (images, volumes, build cache, networks)
#   --images       Remove unused images
#   --volumes      Remove unused volumes (WARNING: destroys data!)
#   --build-cache  Remove build cache
#   --networks     Remove unused networks (WARNING: may affect Compose-managed networks!)
#   --dry-run      Show what would be cleaned without actually cleaning
#
# ⚠️  IMPORTANT: After running '--all' or '--networks', Docker Compose may
#    fail with "network not found" if the compose-managed network was pruned.
#    Run './scripts/preflight-check.sh --fix' to restore it before composing up.
# ============================================

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
  echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
  echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

show_disk_usage() {
  echo "=== Current Disk Usage ==="
  docker system df
  echo ""
  echo "=== Root Partition ==="
  df -h / | tail -1
}

# Parse arguments
DRY_RUN=false
CLEAN_IMAGES=false
CLEAN_VOLUMES=false
CLEAN_BUILD_CACHE=false
CLEAN_NETWORKS=false
CLEAN_ALL=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --all)
      CLEAN_ALL=true
      shift
      ;;
    --images)
      CLEAN_IMAGES=true
      shift
      ;;
    --volumes)
      CLEAN_VOLUMES=true
      shift
      ;;
    --build-cache)
      CLEAN_BUILD_CACHE=true
      shift
      ;;
    --networks)
      CLEAN_NETWORKS=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help | -h)
      head -30 "$0" | grep -v '^#!/bin/bash' | sed 's/^# \?//'
      exit 0
      ;;
    *)
      log_error "Unknown option: $1"
      exit 1
      ;;
  esac
done

# If --all is specified, clean everything
if $CLEAN_ALL; then
  CLEAN_IMAGES=true
  CLEAN_VOLUMES=true
  CLEAN_BUILD_CACHE=true
  CLEAN_NETWORKS=true
fi

# Show current state
echo ""
log_info "Docker Cleanup Script"
echo ""
show_disk_usage
echo ""

if $DRY_RUN; then
  log_warn "DRY RUN - No changes will be made"
  echo ""
fi

# ── Pre-flight warning for network cleanup ──
if $CLEAN_NETWORKS && ! $DRY_RUN; then
  log_warn "Network pruning will remove unused Docker networks,"
  log_warn "including any Compose-managed networks (like 'ytprocessor-network')"
  log_warn "if no containers are currently attached to them."
  echo ""
  log_warn "After cleanup, run './scripts/preflight-check.sh --fix' before"
  log_warn "starting the stack again, to ensure the network is properly recreated."
  echo ""
  read -p "Are you sure you want to prune networks? (yes/no) " -r
  if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    log_info "Aborting network cleanup"
    CLEAN_NETWORKS=false
  fi
  echo ""
fi

# Confirm if cleaning volumes (destructive)
if $CLEAN_VOLUMES && ! $DRY_RUN; then
  log_warn "WARNING: Volume pruning will delete unused volumes!"
  log_warn "This includes named volumes that are not attached to a container."
  log_warn "Data in these volumes will be PERMANENTLY LOST."
  echo ""
  read -p "Are you sure you want to continue? (yes/no) " -r
  if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    log_info "Aborting volume cleanup"
    CLEAN_VOLUMES=false
  fi
  echo ""
fi

# ── Build the prune command ──
# NOTE: We use individual commands instead of 'docker system prune'
# because 'docker system prune --volumes' doesn't allow granular
# control over which resources are cleaned.

if $CLEAN_IMAGES; then
  log_info "Removing unused images..."
  if $DRY_RUN; then
    echo "  Would run: docker image prune -a -f"
  else
    docker image prune -a -f
  fi
  echo ""
fi

if $CLEAN_VOLUMES; then
  log_info "Removing unused volumes..."
  if $DRY_RUN; then
    echo "  Would run: docker volume prune -f"
  else
    docker volume prune -f
  fi
  echo ""
fi

if $CLEAN_NETWORKS; then
  log_info "Removing unused networks..."
  if $DRY_RUN; then
    echo "  Would run: docker network prune -f"
  else
    docker network prune -f
    echo ""
    log_warn "Networks pruned. Run './scripts/preflight-check.sh --fix' before next compose up."
  fi
  echo ""
fi

if $CLEAN_BUILD_CACHE; then
  log_info "Removing build cache..."
  if $DRY_RUN; then
    echo "  Would run: docker builder prune -a -f"
  else
    docker builder prune -a -f
  fi
  echo ""
fi

# ── If nothing was cleaned, show available options ──
if ! $CLEAN_IMAGES && ! $CLEAN_VOLUMES && ! $CLEAN_BUILD_CACHE && ! $CLEAN_NETWORKS; then
  if ! $DRY_RUN; then
    log_warn "No cleanup options selected. Use --help to see available options."
  fi
fi

if $DRY_RUN; then
  log_warn "Dry run complete - no changes made"
else
  log_info "Cleanup complete!"
  echo ""
  show_disk_usage
  echo ""
  log_info "Run './scripts/preflight-check.sh' before the next 'docker compose up'"
  log_info "to verify the environment is ready."
fi
