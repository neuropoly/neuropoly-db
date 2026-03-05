#!/bin/bash
# .devcontainer/postCreate.sh — runs once inside the workspace container after creation
set -e

cd /workspaces/neuropoly-db

# ── 0. Fix volume ownership ────────────────────────────────────────────────
# The venv-data named volume is created as root:root by Docker. Claim it for
# the current user (vscode, uid 1000) so pip can write into it.
# This is a no-op on subsequent runs once the volume is already owned correctly.
sudo chown -R "$(id -u):$(id -g)" .venv 2>/dev/null || true

echo "──────────────────────────────────────────────────────────"
echo " NeuroPoly DB — environment setup"
echo "──────────────────────────────────────────────────────────"

# ── 1. Python virtual environment ─────────────────────────────────────────
echo ""
if [ -f ".venv/bin/activate" ]; then
    echo "==> .venv already exists — skipping creation."
    source .venv/bin/activate
else
    echo "==> Creating virtual environment (.venv)..."
    python3 -m venv .venv
    source .venv/bin/activate
fi

# ── 2. PyTorch CPU-only ───────────────────────────────────────────────────
# CPU-only build works on any hardware and avoids CUDA compatibility issues.
# Hash-based check: reinstalls automatically when pytorch-requirements.txt changes.
echo ""
_PT_HASH="$(md5sum pytorch-requirements.txt | awk '{print $1}')"
_PT_HASH_FILE=".venv/.pt-req-hash"
if [ -f "$_PT_HASH_FILE" ] && [ "$(cat "$_PT_HASH_FILE")" = "$_PT_HASH" ]; then
    echo "==> PyTorch up to date (pytorch-requirements.txt unchanged) — skipping."
else
    echo "==> Installing PyTorch (CPU-only)..."
    pip install --quiet -r pytorch-requirements.txt \
        --index-url https://download.pytorch.org/whl/cpu
    echo "$_PT_HASH" > "$_PT_HASH_FILE"
fi

# ── 3. Project dependencies ───────────────────────────────────────────────
# Hash-based check: reinstalls automatically when requirements.txt changes.
# This ensures stale venv-data volumes are refreshed when dependencies change.
echo ""
_REQ_HASH="$(md5sum requirements.txt | awk '{print $1}')"
_REQ_HASH_FILE=".venv/.req-hash"
if [ -f "$_REQ_HASH_FILE" ] && [ "$(cat "$_REQ_HASH_FILE")" = "$_REQ_HASH" ]; then
    echo "==> Project dependencies up to date (requirements.txt unchanged) — skipping."
else
    echo "==> Installing project dependencies..."
    pip install --quiet -r requirements.txt
    echo "$_REQ_HASH" > "$_REQ_HASH_FILE"
fi

# ── 4. Jupyter kernel ─────────────────────────────────────────────────────
# Always re-register (fast, <1s). Uses --sys-prefix so the kernel spec lives
# inside .venv/ and persists with the venv-data volume across rebuilds.
echo ""
echo "==> Registering Jupyter kernel (sys-prefix)..."
python -m ipykernel install --sys-prefix \
    --name neuropoly-db \
    --display-name "Python (neuropoly-db)"

# ── 5. Summary ────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────────────────────"
echo " ✅  Setup complete"
echo ""
echo "   Python  : $(python --version)"
echo "   Kernel  : Python (neuropoly-db)"
echo ""
echo "   Services (internal Docker network):"
echo "   ES      : ${ES_HOST:-http://elasticsearch:9200}"
echo "   Kibana  : ${KIBANA_URL:-http://kibana:5601}"
echo "   Ollama  : ${OLLAMA_HOST:-http://ollama:11434}"
echo ""
echo "   VS Code will forward these ports to localhost automatically."
echo "   Access Kibana at: http://localhost:5601"
echo ""
echo "   To enable LLM query expansion, pull a model:"
echo "   docker exec -it ollama-dev ollama pull llama3"
echo "   (or llama3.2:1b for a faster ~1 GB model)"
echo ""
echo "   If Elasticsearch fails to start, ensure vm.max_map_count is set"
echo "   on your Docker host:"
echo "   sudo sysctl -w vm.max_map_count=262144"
echo "──────────────────────────────────────────────────────────"
