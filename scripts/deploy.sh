#!/bin/bash
# =============================================================================
# NeuroPoly DB — Production Deployment Script
# =============================================================================
#
# This script deploys the production stack with security enabled.
#
# Usage:
#   ./scripts/deploy.sh [OPTIONS]
#
# Options:
#   --dev         Deploy development stack (no security)
#   --prod        Deploy production stack (with security) [default]
#   --stop        Stop all services
#   --restart     Restart all services
#   --reset       Reset everything (⚠️ deletes all data)
#
# =============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default mode
MODE="prod"
ACTION="deploy"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dev)
            MODE="dev"
            shift
            ;;
        --prod)
            MODE="prod"
            shift
            ;;
        --stop)
            ACTION="stop"
            shift
            ;;
        --restart)
            ACTION="restart"
            shift
            ;;
        --reset)
            ACTION="reset"
            shift
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Determine compose file
if [ "$MODE" = "prod" ]; then
    COMPOSE_FILE="docker-compose.prod.yml"
    echo -e "${BLUE}Using PRODUCTION configuration${NC}"
else
    COMPOSE_FILE="docker-compose.yml"
    echo -e "${YELLOW}Using DEVELOPMENT configuration${NC}"
fi

cd "$PROJECT_ROOT"

# =============================================================================
# Pre-flight Checks
# =============================================================================

check_prerequisites() {
    echo -e "${BLUE}Checking prerequisites...${NC}"
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}Error: Docker is not installed${NC}"
        exit 1
    fi
    
    # Check Docker Compose
    if ! docker compose version &> /dev/null; then
        echo -e "${RED}Error: Docker Compose is not installed${NC}"
        exit 1
    fi
    
    # Check vm.max_map_count
    if [ "$(uname)" = "Linux" ]; then
        MAX_MAP_COUNT=$(sysctl -n vm.max_map_count)
        if [ "$MAX_MAP_COUNT" -lt 262144 ]; then
            echo -e "${YELLOW}Warning: vm.max_map_count is too low ($MAX_MAP_COUNT)${NC}"
            echo -e "${YELLOW}Run: sudo sysctl -w vm.max_map_count=262144${NC}"
            read -p "Continue anyway? (y/n) " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                exit 1
            fi
        fi
    fi
    
    # Check .env file for production
    if [ "$MODE" = "prod" ]; then
        if [ ! -f .env ]; then
            echo -e "${YELLOW}Warning: .env file not found${NC}"
            echo -e "${YELLOW}Creating .env with default passwords (CHANGE THESE!)${NC}"
            cat > .env << EOF
# Elasticsearch
ELASTIC_PASSWORD=changeme_elastic_$(openssl rand -hex 8)

# Kibana
KIBANA_PASSWORD=changeme_kibana_$(openssl rand -hex 8)

# Redis
REDIS_PASSWORD=changeme_redis_$(openssl rand -hex 8)
EOF
            echo -e "${GREEN}Created .env file with random passwords${NC}"
            echo -e "${YELLOW}Please review and update passwords in .env before deploying to production${NC}"
        fi
    fi
    
    echo -e "${GREEN}✓ Prerequisites checked${NC}"
}

# =============================================================================
# Actions
# =============================================================================

deploy_stack() {
    echo -e "${BLUE}Deploying NeuroPoly DB...${NC}"
    
    # Pull images
    echo -e "${BLUE}Pulling Docker images...${NC}"
    docker compose -f "$COMPOSE_FILE" pull
    
    # Start services
    echo -e "${BLUE}Starting services...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d
    
    # Wait for Elasticsearch
    echo -e "${BLUE}Waiting for Elasticsearch to be ready...${NC}"
    sleep 5
    
    MAX_WAIT=120
    ELAPSED=0
    while [ $ELAPSED -lt $MAX_WAIT ]; do
        if docker compose -f "$COMPOSE_FILE" ps elasticsearch | grep -q "healthy"; then
            echo -e "${GREEN}✓ Elasticsearch is ready${NC}"
            break
        fi
        echo -n "."
        sleep 2
        ELAPSED=$((ELAPSED + 2))
    done
    
    if [ $ELAPSED -ge $MAX_WAIT ]; then
        echo -e "${RED}Error: Elasticsearch failed to start${NC}"
        echo -e "${YELLOW}Check logs: docker compose -f $COMPOSE_FILE logs elasticsearch${NC}"
        exit 1
    fi
    
    # Show status
    echo -e "\n${GREEN}✓ Deployment complete!${NC}\n"
    docker compose -f "$COMPOSE_FILE" ps
    
    # Show access URLs
    echo -e "\n${BLUE}Access URLs:${NC}"
    echo -e "  Elasticsearch: ${GREEN}http://localhost:9200${NC}"
    echo -e "  Kibana:        ${GREEN}http://localhost:5601${NC}"
    echo -e "  Redis:         ${GREEN}localhost:6379${NC}"
    
    if [ "$MODE" = "prod" ]; then
        echo -e "\n${YELLOW}Production mode - Security enabled${NC}"
        echo -e "Default credentials (CHANGE THESE!):"
        echo -e "  Username: ${GREEN}elastic${NC}"
        echo -e "  Password: ${GREEN}$(grep ELASTIC_PASSWORD .env | cut -d= -f2)${NC}"
        echo -e "\nTo create API key for application:"
        echo -e "${BLUE}  curl -X POST 'http://localhost:9200/_security/api_key' \\${NC}"
        echo -e "${BLUE}    -u elastic:<password> \\${NC}"
        echo -e "${BLUE}    -H 'Content-Type: application/json' \\${NC}"
        echo -e "${BLUE}    -d '{\"name\":\"neuropoly-db\",\"role_descriptors\":{\"neuropoly\":{\"cluster\":[\"all\"],\"index\":[{\"names\":[\"neuroimaging*\"],\"privileges\":[\"all\"]}]}}}'${NC}"
    fi
}

stop_stack() {
    echo -e "${BLUE}Stopping NeuroPoly DB...${NC}"
    docker compose -f "$COMPOSE_FILE" stop
    echo -e "${GREEN}✓ Services stopped${NC}"
}

restart_stack() {
    echo -e "${BLUE}Restarting NeuroPoly DB...${NC}"
    docker compose -f "$COMPOSE_FILE" restart
    echo -e "${GREEN}✓ Services restarted${NC}"
}

reset_stack() {
    echo -e "${RED}⚠️  WARNING: This will delete all indexed data!${NC}"
    read -p "Are you sure? Type 'yes' to confirm: " -r
    if [ "$REPLY" != "yes" ]; then
        echo "Aborted"
        exit 0
    fi
    
    echo -e "${BLUE}Stopping services...${NC}"
    docker compose -f "$COMPOSE_FILE" down -v
    
    echo -e "${GREEN}✓ All services stopped and volumes removed${NC}"
    echo -e "${YELLOW}Run './scripts/deploy.sh' to redeploy${NC}"
}

# =============================================================================
# Main
# =============================================================================

check_prerequisites

case $ACTION in
    deploy)
        deploy_stack
        ;;
    stop)
        stop_stack
        ;;
    restart)
        restart_stack
        ;;
    reset)
        reset_stack
        ;;
esac
