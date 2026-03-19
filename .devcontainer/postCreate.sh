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

# ── 4. Terminal header hook ───────────────────────────────────────────────
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

# ── 5. Summary ────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────────────────────"
echo " ✅  Setup complete"
echo ""
echo "   Python  : $(python --version)"
echo "   Kernel  : Python (neuropoly-db)"
echo ""
echo "   Open a new VS Code terminal to view the endpoint header."
echo "──────────────────────────────────────────────────────────"
