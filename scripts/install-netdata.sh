#!/bin/bash
# =============================================================================
# NetData Installation Script for Vooglaadija
# =============================================================================
#
# This script helps set up NetData for monitoring the Vooglaadija application.
#
# Usage:
#   ./scripts/install-netdata.sh --help              Show help
#   ./scripts/install-netdata.sh --docker            Setup Docker Compose monitoring
#   ./scripts/install-netdata.sh --host             Install NetData on host
#   ./scripts/install-netdata.sh --claim            Claim nodes to NetData Cloud
#
# Requirements:
#   - Docker and docker-compose (for Docker installation)
#   - curl, jq (for claim process)
#
# =============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
NETDATA_VERSION="stable"

# =============================================================================
# Helper Functions
# =============================================================================

log_info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

show_help() {
  cat <<EOF
NetData Installation Script for Vooglaadija

USAGE:
    $0 [OPTIONS]

OPTIONS:
    --docker        Setup NetData Docker agents via docker-compose
    --host          Install NetData directly on the host (Linux only)
    --claim         Claim nodes to NetData Cloud
    --status        Check NetData agent status
    --dashboards    Open NetData dashboards in browser
    --help          Show this help message

EXAMPLES:
    # Setup Docker-based monitoring
    $0 --docker

    # Install on host (for bare metal)
    $0 --host

    # Claim running nodes to NetData Cloud
    $0 --claim

ENVIRONMENT VARIABLES:
    NETDATA_CLAIM_TOKEN    Claim token from app.netdata.cloud
    NETDATA_CLAIM_URL      Claim URL (default: https://app.netdata.cloud)
    NETDATA_CLAIM_ROOMS    Room ID(s) for organizing nodes

EOF
}

# =============================================================================
# Docker Installation
# =============================================================================

install_docker() {
  log_info "Setting up NetData Docker agents..."

  # Check if monitoring compose file exists
  if [[ ! -f "$PROJECT_DIR/docker-compose.monitoring.yml" ]]; then
    log_error "docker-compose.monitoring.yml not found!"
    exit 1
  fi

  # Check if .env file exists
  if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    log_warning ".env file not found, creating from .env.example"
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  fi

  # Prompt for NetData claim token if not set
  if [[ -z "${NETDATA_CLAIM_TOKEN:-}" ]]; then
    echo ""
    log_info "To claim nodes to NetData Cloud, you need a claim token."
    echo ""
    echo "Steps to get your claim token:"
    echo "  1. Go to https://app.netdata.cloud"
    echo "  2. Sign up / Log in"
    echo "  3. Create a Space (or use default)"
    echo "  4. Create a Room for vooglaadija"
    echo "  5. Go to Nodes -> Claim nodes"
    echo "  6. Copy the claim token"
    echo ""
    read -rp "Enter your NetData Claim Token: " claim_token

    if [[ -n "$claim_token" ]]; then
      # Add to .env file using safe parsing (atomic write with temp file)
      # Use ASCII unit separator (0x1F) as sed delimiter to avoid conflicts with token content
      if grep -q "NETDATA_CLAIM_TOKEN" "$PROJECT_DIR/.env" 2>/dev/null; then
        # Create backup first
        cp "$PROJECT_DIR/.env" "$PROJECT_DIR/.env.bak"
        # Escape special characters in token for sed replacement
        # Use $'\x1F' (ASCII unit separator) as delimiter instead of /
        safe_token="$(printf '%s' "$claim_token" | sed 's/[&/\]/\\&/g')"
        # Use 0x1F as field separator for sed to avoid delimiter conflicts
        sed $'s\\\x1FNETDATA_CLAIM_TOKEN=.*\\\x1FNETDATA_CLAIM_TOKEN='"$safe_token"$'\\\x1F' "$PROJECT_DIR/.env" >"$PROJECT_DIR/.env.tmp"
        if mv "$PROJECT_DIR/.env.tmp" "$PROJECT_DIR/.env"; then
          rm -f "$PROJECT_DIR/.env.bak"
        else
          # Restore from backup if mv fails
          mv "$PROJECT_DIR/.env.bak" "$PROJECT_DIR/.env" 2>/dev/null || true
          log_error "Failed to update .env file"
          exit 1
        fi
      else
        echo "NETDATA_CLAIM_TOKEN=$claim_token" >>"$PROJECT_DIR/.env"
      fi
      export NETDATA_CLAIM_TOKEN="$claim_token"
    fi
  fi

  # Pull NetData image
  log_info "Pulling NetData image..."
  docker pull netdata/netdata:$NETDATA_VERSION

  # Start monitoring services
  log_info "Starting NetData agents..."
  cd "$PROJECT_DIR"
  docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d

  log_success "NetData Docker agents started!"
  echo ""
  log_info "Waiting for NetData to initialize..."
  sleep 5

  # Show status
  echo ""
  docker compose -f docker-compose.yml -f docker-compose.monitoring.yml ps netdata-api netdata-worker netdata-db netdata-redis

  echo ""
  log_success "NetData installation complete!"
  echo ""
  echo "Next steps:"
  echo "  - View NetData dashboard: http://localhost:19999 (if standalone)"
  echo "  - Or use NetData Cloud: https://app.netdata.cloud"
  echo ""
}

# =============================================================================
# Host Installation (Linux only)
# =============================================================================

install_host() {
  log_info "Installing NetData on host..."

  # Check if running as root
  if [[ $EUID -ne 0 ]]; then
    log_error "Host installation requires root privileges."
    log_info "Please run: sudo $0 --host"
    exit 1
  fi

  # Detect distribution
  if [[ -f /etc/debian_version ]]; then
    log_info "Detected Debian/Ubuntu system"
    apt-get update
    apt-get install -y netdata

  elif [[ -f /etc/redhat-release ]]; then
    log_info "Detected RedHat/CentOS/Fedora system"
    yum install -y netdata

  elif [[ -f /etc/alpine-release ]]; then
    log_info "Detected Alpine Linux"
    apk add netdata

  else
    log_error "Unsupported distribution. Try Docker installation instead."
    exit 1
  fi

  # Configure NetData
  log_info "Configuring NetData..."

  # Enable Docker collector
  if [[ -f /etc/netdata/go.d/docker.conf ]]; then
    sed -i 's/enabled = no/enabled = yes/' /etc/netdata/go.d/docker.conf
  fi

  # Configure claim token if provided
  if [[ -n "${NETDATA_CLAIM_TOKEN:-}" ]]; then
    log_info "Configuring NetData Cloud claim..."
    cat >/var/lib/netdata/cloud.d/cloud.conf <<EOF
[global]
  enabled = yes
  claim token = $NETDATA_CLAIM_TOKEN
  claim url = ${NETDATA_CLAIM_URL:-https://app.netdata.cloud}
EOF
  fi

  # Restart NetData
  log_info "Starting NetData service..."
  systemctl enable netdata
  systemctl restart netdata

  log_success "NetData installed and started!"
  echo ""
  echo "Dashboard available at: http://localhost:19999"
  echo "Configuration: /etc/netdata/netdata.conf"
  echo ""
}

# =============================================================================
# Claim Nodes to Cloud
# =============================================================================

claim_nodes() {
  log_info "Claiming NetData nodes to NetData Cloud..."

  # Check for claim token
  if [[ -z "${NETDATA_CLAIM_TOKEN:-}" ]]; then
    # Safely read NETDATA_CLAIM_TOKEN from .env without executing the file
    if [[ -f "$PROJECT_DIR/.env" ]]; then
      # Read only NETDATA_CLAIM_TOKEN, NETDATA_CLAIM_URL, NETDATA_CLAIM_ROOMS keys
      # Use grep/awk to extract value-only, ignoring comments and blank lines
      NETDATA_CLAIM_TOKEN="$(grep -E "^NETDATA_CLAIM_TOKEN=" "$PROJECT_DIR/.env" 2>/dev/null | head -1 | sed 's/^NETDATA_CLAIM_TOKEN=[      ]*//' | sed 's/^["'"'"']//;s/["'"'"']$//' | awk '{print $1}')"
      NETDATA_CLAIM_URL="$(grep -E "^NETDATA_CLAIM_URL=" "$PROJECT_DIR/.env" 2>/dev/null | head -1 | sed 's/^NETDATA_CLAIM_URL=[      ]*//' | sed 's/^["'"'"']//;s/["'"'"']$//' | awk '{print $1}')"
      NETDATA_CLAIM_ROOMS="$(grep -E "^NETDATA_CLAIM_ROOMS=" "$PROJECT_DIR/.env" 2>/dev/null | head -1 | sed 's/^NETDATA_CLAIM_ROOMS=[      ]*//' | sed 's/^["'"'"']//;s/["'"'"']$//' | awk '{print $1}')"
    fi
  fi

  if [[ -z "${NETDATA_CLAIM_TOKEN:-}" ]]; then
    log_error "NETDATA_CLAIM_TOKEN not set!"
    log_info "Get your token at: https://app.netdata.cloud -> Nodes -> Claim"
    exit 1
  fi

  # Claim token
  TOKEN="$NETDATA_CLAIM_TOKEN"
  URL="${NETDATA_CLAIM_URL:-https://app.netdata.cloud}"
  ROOM="${NETDATA_CLAIM_ROOMS:-}"

  # Get list of NetData containers
  containers=$(docker ps --format '{{.Names}}' | grep netdata || true)

  if [[ -z "$containers" ]]; then
    log_warning "No NetData containers found"

    # Try host installation
    if command -v netdata-claim.sh &>/dev/null; then
      log_info "Trying host installation claim..."
      netdata-claim.sh -token="$TOKEN" -url="$URL" ${ROOM:+-rooms=$ROOM}
    fi
  else
    for container in $containers; do
      log_info "Claiming container: $container"
      docker exec "$container" netdata-claim.sh -token="$TOKEN" -url="$URL" ${ROOM:+-rooms=$ROOM} || true
    done
  fi

  log_success "Claim process initiated!"
  log_info "Check NetData Cloud dashboard to verify nodes appeared."
}

# =============================================================================
# Check Status
# =============================================================================

check_status() {
  log_info "Checking NetData agent status..."

  # Check Docker containers
  echo ""
  echo "=== Docker NetData Agents ==="
  docker ps --filter "name=netdata*" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "Docker not available or no NetData containers"

  # Check host installation
  if command -v netdata &>/dev/null; then
    echo ""
    echo "=== Host NetData ==="
    systemctl status netdata --no-pager || true
    echo ""
    if curl -s http://localhost:19999/api/v1/info | jq -r '.version' >/dev/null 2>&1; then
      log_success "NetData is running"
    else
      log_warning "NetData not responding"
    fi
  fi

  # Check if cloud-connected
  echo ""
  echo "=== NetData Cloud Status ==="
  for container in $(docker ps --format '{{.Names}}' 2>/dev/null | grep netdata || true); do
    echo "Container: $container"
    docker exec "$container" cat /var/lib/netdata/cloud.d/cloud_link.txt 2>/dev/null | head -5 || echo "  Not cloud-connected"
  done
}

# =============================================================================
# Open Dashboards
# =============================================================================

open_dashboards() {
  log_info "Opening NetData dashboards..."

  if command -v xdg-open &>/dev/null; then
    xdg-open "https://app.netdata.cloud" 2>/dev/null || true
  fi

  if command -v open &>/dev/null; then
    open "https://app.netdata.cloud" 2>/dev/null || true
  fi

  echo "Please open:"
  echo "  - NetData Cloud: https://app.netdata.cloud"
  echo "  - Local (if running): http://localhost:19999"
}

# =============================================================================
# Main Entry Point
# =============================================================================

main() {
  # Parse arguments
  case "${1:-}" in
    --docker)
      install_docker
      ;;
    --host)
      install_host
      ;;
    --claim)
      claim_nodes
      ;;
    --status)
      check_status
      ;;
    --dashboards)
      open_dashboards
      ;;
    --help | -h)
      show_help
      ;;
    "")
      show_help
      ;;
    *)
      log_error "Unknown option: $1"
      show_help
      exit 1
      ;;
  esac
}

main "$@"
