#!/bin/bash
# NetData Cloud Claim Script for Vooglaadija
# Claims NetData agents to NetData Cloud for centralized monitoring
#
# Usage:
#   ./scripts/claim-netdata.sh [--token TOKEN] [--url URL] [--rooms ROOMS]
#
# Prerequisites:
#   1. Create a free account at https://app.netdata.cloud
#   2. Create a Space and Room
#   3. Get the claim token from the Room settings
#
# Environment Variables:
#   NETDATA_CLAIM_TOKEN - Your claim token (required)
#   NETDATA_CLAIM_URL   - Claim URL (optional, defaults to NetData Cloud)
#   NETDATA_CLAIM_ROOMS - Room ID(s) to add nodes to (optional)

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
  echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
  echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

display_command() {
  local -a rendered=()
  local part

  for part in "$@"; do
    if [[ "${part}" == -token=* ]]; then
      rendered+=("-token=<redacted>")
    else
      rendered+=("${part}")
    fi
  done

  printf '%q ' "${rendered[@]}"
  printf '\n'
}

show_help() {
  cat <<EOF
NetData Cloud Claim Script for Vooglaadija

Usage:
    $0 [OPTIONS]

Options:
    --token TOKEN    Your NetData Cloud claim token
    --url URL       Claim URL (default: https://app.netdata.cloud/api/v1/node/claim)
    --rooms ROOMS   Room ID(s) to add nodes to (comma-separated)
    --all           Claim all NetData containers
    --api           Claim only the API NetData agent
    --worker        Claim only the Worker NetData agent
    --dry-run       Show claim commands without executing
    --help, -h      Show this help message

Environment Variables:
    NETDATA_CLAIM_TOKEN    Your claim token (required)
    NETDATA_CLAIM_URL      Claim URL (optional)
    NETDATA_CLAIM_ROOMS    Room IDs (optional)

Examples:
    # Claim all nodes with token from environment
    NETDATA_CLAIM_TOKEN=abc123 ./scripts/claim-netdata.sh

    # Claim with explicit token and rooms
    ./scripts/claim-netdata.sh --token abc123 --rooms room-id-1,room-id-2

    # Preview what would be claimed
    ./scripts/claim-netdata.sh --token abc123 --dry-run

EOF
}

# Parse arguments
CLAIM_TOKEN=""
CLAIM_URL="https://app.netdata.cloud"
CLAIM_ROOMS=""
CLAIM_ALL=true
CLAIM_API=false
CLAIM_WORKER=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --token)
      CLAIM_TOKEN="$2"
      shift 2
      ;;
    --url)
      CLAIM_URL="$2"
      shift 2
      ;;
    --rooms)
      CLAIM_ROOMS="$2"
      shift 2
      ;;
    --all)
      CLAIM_ALL=true
      CLAIM_API=false
      CLAIM_WORKER=false
      shift
      ;;
    --api)
      CLAIM_ALL=false
      CLAIM_API=true
      shift
      ;;
    --worker)
      CLAIM_ALL=false
      CLAIM_WORKER=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help | -h)
      show_help
      exit 0
      ;;
    *)
      log_error "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Check for token in environment if not provided
if [[ -z "$CLAIM_TOKEN" ]]; then
  if [[ -z "${NETDATA_CLAIM_TOKEN:-}" ]]; then
    log_error "Claim token required. Set NETDATA_CLAIM_TOKEN env var or use --token"
    echo ""
    show_help
    exit 1
  fi
  CLAIM_TOKEN="$NETDATA_CLAIM_TOKEN"
fi

# Use environment URL if different
if [[ -n "${NETDATA_CLAIM_URL:-}" ]]; then
  CLAIM_URL="$NETDATA_CLAIM_URL"
fi

# Build rooms argument
ROOMS_ARG=""
if [[ -n "$CLAIM_ROOMS" ]]; then
  ROOMS_ARG="$CLAIM_ROOMS"
elif [[ -n "${NETDATA_CLAIM_ROOMS:-}" ]]; then
  ROOMS_ARG="$NETDATA_CLAIM_ROOMS"
fi

# Function to claim a container
claim_container() {
  local container_name=$1
  local hostname=$2

  echo ""
  log_info "Claiming $container_name (hostname: $hostname)..."

  local -a cmd=("docker" "exec" "$container_name" "netdata-claim.sh" "-token=$CLAIM_TOKEN" "-url=$CLAIM_URL")
  if [[ -n "$ROOMS_ARG" ]]; then
    cmd+=("-rooms=$ROOMS_ARG")
  fi

  if $DRY_RUN; then
    local display
    display="$(display_command "${cmd[@]}")"
    log_warn "DRY RUN: ${display}"
  else
    if "${cmd[@]}"; then
      log_info "Successfully claimed $container_name"
    else
      log_error "Failed to claim $container_name"
    fi
  fi
}

# Get list of netdata containers
get_netdata_containers() {
  docker ps --format '{{.Names}}' | grep netdata
}

echo ""
log_info "NetData Cloud Claim Script"
echo ""
log_info "Token: <redacted>"
log_info "URL: ${CLAIM_URL}"
if [[ -n "${ROOMS_ARG}" ]]; then
  log_info "Rooms: ${ROOMS_ARG}"
fi
echo ""

# Check if any netdata containers are running
CONTAINERS=$(get_netdata_containers)
if [[ -z "$CONTAINERS" ]]; then
  log_error "No NetData containers running. Start them with:"
  echo "  docker-compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d"
  exit 1
fi

# Show containers
log_info "Found NetData containers:"
echo "$CONTAINERS" | while read -r name; do
  echo "  - $name"
done
echo ""

if $DRY_RUN; then
  log_warn "DRY RUN - No changes will be made"
  echo ""
fi

# Claim containers based on flags
if $CLAIM_ALL || $CLAIM_API; then
  if echo "$CONTAINERS" | grep -q "netdata-api"; then
    claim_container "ytprocessor-netdata-api" "vooglaadija-api"
  fi
fi

if $CLAIM_ALL || $CLAIM_WORKER; then
  if echo "$CONTAINERS" | grep -q "netdata-worker"; then
    claim_container "ytprocessor-netdata-worker" "vooglaadija-worker"
  fi
fi

if $CLAIM_ALL; then
  # Claim DB and Redis agents too
  if echo "$CONTAINERS" | grep -q "netdata-db"; then
    claim_container "ytprocessor-netdata-db" "vooglaadija-db"
  fi
  if echo "$CONTAINERS" | grep -q "netdata-redis"; then
    claim_container "ytprocessor-netdata-redis" "vooglaadija-redis"
  fi
fi

echo ""
if $DRY_RUN; then
  log_warn "Dry run complete - no changes made"
else
  log_info "Claim process complete!"
  log_info "Visit https://app.netdata.cloud to verify nodes are connected"
fi
