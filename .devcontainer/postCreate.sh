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

# ── 2. Project dependencies ───────────────────────────────────────────────
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

# ── 3. Jupyter kernel ─────────────────────────────────────────────────────
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
echo ""
echo "   VS Code will forward these ports to localhost automatically."
echo ""
echo "──────────────────────────────────────────────────────────"
