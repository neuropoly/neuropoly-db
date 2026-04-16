#!/bin/bash
# .devcontainer/postCreate.sh — runs once inside the workspace container after creation
set -e

cd /workspaces/neuropoly-db

# ── 0. Fix volume ownership ────────────────────────────────────────────────
# The venv-data named volume is created as root:root by Docker. Claim it for
# the current user (vscode, uid 1000) so Python package tools can write into it.
# This is a no-op on subsequent runs once the volume is already owned correctly.
sudo chown -R "$(id -u):$(id -g)" .venv 2>/dev/null || true

echo "──────────────────────────────────────────────────────────"
echo " NeuroPoly DB — environment setup"
echo "──────────────────────────────────────────────────────────"

# ── 1. Python virtual environment ─────────────────────────────────────────
echo ""
export UV_PROJECT_ENVIRONMENT=".venv"

if [ -f ".venv/bin/activate" ]; then
    echo "==> .venv already exists — skipping creation."
    source .venv/bin/activate
else
    echo "==> Creating virtual environment with uv (.venv)..."
    uv venv --allow-existing .venv
    source .venv/bin/activate
fi

# ── 2. Project installation ───────────────────────────────────────────────
# Hash-based check: reinstalls automatically when pyproject.toml / uv.lock change.
# This ensures stale venv-data volumes are refreshed when project dependencies change.
echo ""
if [ -f "uv.lock" ]; then
    _REQ_HASH="$(cat pyproject.toml uv.lock | md5sum | awk '{print $1}')"
else
    _REQ_HASH="$(md5sum pyproject.toml | awk '{print $1}')"
fi
_REQ_HASH_FILE=".venv/.req-hash"
if [ -f "$_REQ_HASH_FILE" ] && [ "$(cat "$_REQ_HASH_FILE")" = "$_REQ_HASH" ]; then
    echo "==> Project install up to date (pyproject.toml/uv.lock unchanged) — skipping."
else
    echo "==> Syncing project with uv..."
    uv sync --active --quiet
    echo "$_REQ_HASH" > "$_REQ_HASH_FILE"
fi

# ── 3. Playwright browser installation ────────────────────────────────────
# Install system dependencies and browser binaries for Playwright automation.
# Required for annotation automation features.
echo ""
echo "==> Installing Playwright system dependencies and browsers..."

# Sync project with annotation-automation extra
uv sync --active --quiet --extra annotation-automation

# Run playwright installation tool from chromium browser package
uv run playwright install chromium --with-deps > /dev/null 2>&1 || {
    echo "   WARNING: Playwright browser installation had issues (may still work)"
}
echo "   ✓ Playwright system dependencies and browsers ready"

# # Install system packages required by Playwright
# sudo apt-get update -qq > /dev/null 2>&1 || true
# sudo apt-get install -qq -y \
#     libgconf-2-4 \
#     libnss3 \
#     libxss1 \
#     libappindicator1 \
#     libindicator7 \
#     xdg-utils \
#     fonts-liberation \
#     libasound2 \
#     > /dev/null 2>&1 || true

# # Install Playwright browsers (uses pre-cached layers if available)
# python -m playwright install --with-deps chromium > /dev/null 2>&1 || {
#     echo "   WARNING: Playwright browser installation had issues (may still work)"
# }
# echo "   ✓ Playwright system dependencies and browsers ready"

# ── 4. Jupyter kernel ─────────────────────────────────────────────────────
# Always re-register (fast, <1s). Uses --sys-prefix so the kernel spec lives
# inside .venv/ and persists with the venv-data volume across rebuilds.
echo ""
echo "==> Registering Jupyter kernel (sys-prefix)..."
python -m ipykernel install --sys-prefix \
    --name neuropoly-db \
    --display-name "Python (neuropoly-db)"

# ── 5. Terminal header hook ───────────────────────────────────────────────
echo ""
echo "==> Installing terminal header hook (.bashrc)..."

HEADER_SCRIPT="/workspaces/neuropoly-db/.devcontainer/terminal-header.sh"
USER_BASHRC="$HOME/.bashrc"
HOOK_BEGIN="# >>> neuropoly terminal header >>>"
HOOK_END="# <<< neuropoly terminal header <<<"

if [ ! -f "$HEADER_SCRIPT" ]; then
    echo "WARNING: Missing terminal header script at $HEADER_SCRIPT"
else
    if [ ! -f "$USER_BASHRC" ]; then
        touch "$USER_BASHRC"
    fi

    if ! grep -Fq "$HOOK_BEGIN" "$USER_BASHRC"; then
        {
            echo ""
            echo "$HOOK_BEGIN"
            echo "if [ -f \"$HEADER_SCRIPT\" ]; then"
            echo "  source \"$HEADER_SCRIPT\""
            echo "fi"
            echo "$HOOK_END"
        } >> "$USER_BASHRC"
        echo "   Added terminal header hook to $USER_BASHRC"
    else
        echo "   Terminal header hook already present in $USER_BASHRC"
    fi
fi

# ── 6. Wireguard setup (if config file present) ───────────────────────────────────────────────
WG_CONFIG="/workspaces/neuropoly-db/wg0.conf"
if [ -f "$WG_CONFIG" ]; then
    echo ""
    echo "==> Wireguard config detected at $WG_CONFIG — setting up wg-quick..."
    sudo cp "$WG_CONFIG" "/etc/wireguard/wg0.conf"
    sudo chmod 600 "/etc/wireguard/wg0.conf"
    echo "   Wireguard config copied to /etc/wireguard/wg0.conf with permissions 600."
    echo "   You can start the Wireguard interface with: sudo wg-quick up wg0"
else
    echo ""
    echo "==> No Wireguard config found at $WG_CONFIG — skipping wg-quick setup."
fi

# ── 6. Summary ────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────────────────────"
echo " ✅  Setup complete"
echo ""
echo "   Python  : $(python --version)"
echo "   Kernel  : Python (neuropoly-db)"
echo ""
echo "   Open a new VS Code terminal to view the endpoint header."
echo "──────────────────────────────────────────────────────────"
