#!/usr/bin/env bash
# test_course.sh — Smoke test for the neuroimaging vector DB course
#
# This script verifies:
# 1. Docker services start correctly
# 2. ES is reachable and healthy
# 3. Kibana is reachable
# 4. The dataset is present
# 5. All notebooks execute without errors
#
# Usage:
#   bash scripts/test_course.sh
#
# Prerequisites:
#   - Docker & Docker Compose installed
#   - Python venv with requirements.txt installed
#   - Dataset downloaded (scripts/download_dataset.sh)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }

echo "========================================"
echo "  Neuroimaging Vector DB — Smoke Test"
echo "========================================"
echo ""

# --- 1. Check Docker services ---
echo "--- Docker Services ---"

if ! command -v docker &>/dev/null; then
    fail "Docker is not installed"
fi

if docker compose ps --format json 2>/dev/null | grep -q "elasticsearch"; then
    pass "Docker Compose services found"
else
    warn "Docker services not running — starting them..."
    docker compose up -d
    echo "Waiting 30s for services to initialize..."
    sleep 30
fi

# --- 2. Check ElasticSearch ---
echo ""
echo "--- ElasticSearch ---"

ES_URL="http://localhost:9200"
MAX_RETRIES=30
RETRY_INTERVAL=5

for i in $(seq 1 $MAX_RETRIES); do
    if curl -sf "$ES_URL/_cluster/health" >/dev/null 2>&1; then
        break
    fi
    if [ "$i" -eq "$MAX_RETRIES" ]; then
        fail "ElasticSearch not reachable at $ES_URL after $((MAX_RETRIES * RETRY_INTERVAL))s"
    fi
    echo "  Waiting for ES... ($i/$MAX_RETRIES)"
    sleep $RETRY_INTERVAL
done

ES_STATUS=$(curl -sf "$ES_URL/_cluster/health" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
ES_VERSION=$(curl -sf "$ES_URL" | python3 -c "import sys,json; print(json.load(sys.stdin)['version']['number'])")
pass "ElasticSearch $ES_VERSION is running (status: $ES_STATUS)"

# --- 3. Check Kibana ---
echo ""
echo "--- Kibana ---"

KIBANA_URL="http://localhost:5601"
for i in $(seq 1 $MAX_RETRIES); do
    if curl -sf "$KIBANA_URL/api/status" >/dev/null 2>&1; then
        break
    fi
    if [ "$i" -eq "$MAX_RETRIES" ]; then
        fail "Kibana not reachable at $KIBANA_URL after $((MAX_RETRIES * RETRY_INTERVAL))s"
    fi
    echo "  Waiting for Kibana... ($i/$MAX_RETRIES)"
    sleep $RETRY_INTERVAL
done
pass "Kibana is running at $KIBANA_URL"

# --- 4. Check dataset ---
echo ""
echo "--- Dataset ---"

DATASET_DIR="data/ds001"
if [ -d "$DATASET_DIR" ]; then
    N_SUBJECTS=$(find "$DATASET_DIR" -maxdepth 1 -type d -name "sub-*" | wc -l)
    pass "Dataset found at $DATASET_DIR ($N_SUBJECTS subjects)"
else
    fail "Dataset not found at $DATASET_DIR — run: bash scripts/download_dataset.sh"
fi

# --- 5. Check Python environment ---
echo ""
echo "--- Python Environment ---"

if python3 -c "import elasticsearch, bids, sentence_transformers" 2>/dev/null; then
    pass "Required Python packages are installed"
else
    fail "Missing Python packages — run: pip install -r requirements.txt"
fi

# --- 6. Execute notebooks ---
echo ""
echo "--- Notebook Execution ---"

NOTEBOOKS=(
    "notebooks/01-setup-and-ingest.ipynb"
    "notebooks/02-keyword-and-filtered-search.ipynb"
    "notebooks/03-vector-search.ipynb"
    # NB4 is mostly Kibana GUI instructions, skip execution
)

for nb in "${NOTEBOOKS[@]}"; do
    echo "  Running $nb ..."
    if jupyter nbconvert --to notebook --execute --ExecutePreprocessor.timeout=300 \
        --output-dir=/tmp "$nb" 2>/dev/null; then
        pass "$nb executed successfully"
    else
        fail "$nb failed to execute"
    fi
done

warn "Notebook 04 (Kibana exploration) skipped — requires manual browser interaction"

# --- 7. Verify indexed data ---
echo ""
echo "--- Index Verification ---"

DOC_COUNT=$(curl -sf "$ES_URL/neuroimaging/_count" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])")
if [ "$DOC_COUNT" -gt 0 ]; then
    pass "Index 'neuroimaging' has $DOC_COUNT documents"
else
    fail "Index 'neuroimaging' is empty"
fi

# --- Summary ---
echo ""
echo "========================================"
echo -e "  ${GREEN}All smoke tests passed!${NC}"
echo "========================================"
echo ""
echo "You're ready to work through the course:"
echo "  1. Read docs/00-overview.md"
echo "  2. Open notebooks in order (01 → 04)"
echo "  3. Open Kibana at http://localhost:5601"
