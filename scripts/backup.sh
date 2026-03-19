#!/bin/bash
# =============================================================================
# NeuroPoly DB — Elasticsearch Backup Script
# =============================================================================
#
# This script creates snapshots of Elasticsearch indices for backup/restore.
#
# Usage:
#   ./scripts/backup.sh [OPTIONS]
#
# Options:
#   --create <name>    Create a new snapshot
#   --restore <name>   Restore from snapshot
#   --list             List all snapshots
#   --delete <name>    Delete a snapshot
#   --setup            Setup snapshot repository
#
# Examples:
#   ./scripts/backup.sh --setup
#   ./scripts/backup.sh --create backup-2026-03-09
#   ./scripts/backup.sh --list
#   ./scripts/backup.sh --restore backup-2026-03-09
#
# =============================================================================

set -e

# Configuration
ES_HOST="${ES_HOST:-http://localhost:9200}"
REPO_NAME="neuropoly-backups"
SNAPSHOT_DIR="/usr/share/elasticsearch/snapshots"
CONTAINER_NAME="${ES_CONTAINER:-neuropoly-es-elasticsearch}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Parse arguments
ACTION=""
SNAPSHOT_NAME=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --create)
            ACTION="create"
            SNAPSHOT_NAME="$2"
            shift 2
            ;;
        --restore)
            ACTION="restore"
            SNAPSHOT_NAME="$2"
            shift 2
            ;;
        --list)
            ACTION="list"
            shift
            ;;
        --delete)
            ACTION="delete"
            SNAPSHOT_NAME="$2"
            shift 2
            ;;
        --setup)
            ACTION="setup"
            shift
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

if [ -z "$ACTION" ]; then
    echo "Usage: $0 [--setup|--create|--restore|--list|--delete] <snapshot-name>"
    exit 1
fi

# Check if Elasticsearch is running
check_es() {
    if ! curl -s "$ES_HOST" > /dev/null 2>&1; then
        echo -e "${RED}Error: Cannot connect to Elasticsearch at $ES_HOST${NC}"
        exit 1
    fi
}

# Setup snapshot repository
setup_repo() {
    echo -e "${BLUE}Setting up snapshot repository...${NC}"
    
    # Create snapshots directory in container
    docker exec "$CONTAINER_NAME" mkdir -p "$SNAPSHOT_DIR" 2>/dev/null || true
    docker exec "$CONTAINER_NAME" chown -R elasticsearch:elasticsearch "$SNAPSHOT_DIR" 2>/dev/null || true
    
    # Register repository
    curl -X PUT "$ES_HOST/_snapshot/$REPO_NAME" \
        -H 'Content-Type: application/json' \
        -d "{
            \"type\": \"fs\",
            \"settings\": {
                \"location\": \"$SNAPSHOT_DIR\",
                \"compress\": true
            }
        }"
    
    echo -e "\n${GREEN}✓ Snapshot repository created: $REPO_NAME${NC}"
}

# Create snapshot
create_snapshot() {
    if [ -z "$SNAPSHOT_NAME" ]; then
        SNAPSHOT_NAME="snapshot-$(date +%Y%m%d-%H%M%S)"
    fi
    
    echo -e "${BLUE}Creating snapshot: $SNAPSHOT_NAME${NC}"
    
    curl -X PUT "$ES_HOST/_snapshot/$REPO_NAME/$SNAPSHOT_NAME?wait_for_completion=true" \
        -H 'Content-Type: application/json' \
        -d '{
            "indices": "neuroimaging*",
            "ignore_unavailable": true,
            "include_global_state": false,
            "metadata": {
                "taken_by": "backup-script",
                "taken_because": "scheduled backup"
            }
        }'
    
    echo -e "\n${GREEN}✓ Snapshot created: $SNAPSHOT_NAME${NC}"
}

# List snapshots
list_snapshots() {
    echo -e "${BLUE}Listing snapshots in repository: $REPO_NAME${NC}\n"
    
    RESPONSE=$(curl -s "$ES_HOST/_snapshot/$REPO_NAME/_all")
    
    echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
if 'snapshots' in data:
    for snap in data['snapshots']:
        print(f\"Name: {snap['snapshot']}\")
        print(f\"  State: {snap['state']}\")
        print(f\"  Start: {snap['start_time']}\")
        print(f\"  Duration: {snap.get('duration_in_millis', 0) / 1000:.1f}s\")
        print(f\"  Indices: {len(snap['indices'])}\")
        print()
else:
    print('No snapshots found')
"
}

# Restore snapshot
restore_snapshot() {
    if [ -z "$SNAPSHOT_NAME" ]; then
        echo -e "${RED}Error: Snapshot name required${NC}"
        exit 1
    fi
    
    echo -e "${YELLOW}⚠️  WARNING: This will restore data from snapshot${NC}"
    read -p "Continue? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
    
    echo -e "${BLUE}Restoring snapshot: $SNAPSHOT_NAME${NC}"
    
    curl -X POST "$ES_HOST/_snapshot/$REPO_NAME/$SNAPSHOT_NAME/_restore" \
        -H 'Content-Type: application/json' \
        -d '{
            "indices": "neuroimaging*",
            "ignore_unavailable": true,
            "include_global_state": false
        }'
    
    echo -e "\n${GREEN}✓ Restore initiated${NC}"
    echo -e "${YELLOW}Monitor progress: curl $ES_HOST/_snapshot/$REPO_NAME/$SNAPSHOT_NAME/_status${NC}"
}

# Delete snapshot
delete_snapshot() {
    if [ -z "$SNAPSHOT_NAME" ]; then
        echo -e "${RED}Error: Snapshot name required${NC}"
        exit 1
    fi
    
    echo -e "${YELLOW}⚠️  Deleting snapshot: $SNAPSHOT_NAME${NC}"
    read -p "Are you sure? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
    
    curl -X DELETE "$ES_HOST/_snapshot/$REPO_NAME/$SNAPSHOT_NAME"
    
    echo -e "\n${GREEN}✓ Snapshot deleted${NC}"
}

# Main
check_es

case $ACTION in
    setup)
        setup_repo
        ;;
    create)
        create_snapshot
        ;;
    list)
        list_snapshots
        ;;
    restore)
        restore_snapshot
        ;;
    delete)
        delete_snapshot
        ;;
esac
