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
    uv sync --active --quiet --dev --extra annotation-automation
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

# ── 5b. SSH agent hook ────────────────────────────────────────────────────
echo ""
echo "==> Installing SSH agent hook (.bashrc)..."

SSH_AGENT_SCRIPT="/workspaces/neuropoly-db/.devcontainer/ssh-agent.sh"
SSH_HOOK_BEGIN="# >>> neuropoly ssh-agent >>>"
SSH_HOOK_END="# <<< neuropoly ssh-agent <<<"

if [ ! -f "$SSH_AGENT_SCRIPT" ]; then
    echo "   WARNING: Missing SSH agent script at $SSH_AGENT_SCRIPT"
else
    if ! grep -Fq "$SSH_HOOK_BEGIN" "$USER_BASHRC"; then
        {
            echo ""
            echo "$SSH_HOOK_BEGIN"
            echo "if [ -f \"$SSH_AGENT_SCRIPT\" ]; then"
            echo "  source \"$SSH_AGENT_SCRIPT\""
            echo "fi"
            echo "$SSH_HOOK_END"
        } >> "$USER_BASHRC"
        echo "   Added SSH agent hook to $USER_BASHRC"
    else
        echo "   SSH agent hook already present in $USER_BASHRC"
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

# ── 7. Openconnect configuration (if wireguard not configured) ──────────────────────────────────────
# Install openconnect and register the `vpnconnect` shell function so users
# without a Wireguard config can connect to PolyMTL via the PolyQuartz / Okta
# interactive flow.  The actual connection is always user-triggered — nothing
# connects automatically here.
if [ ! -f "$WG_CONFIG" ]; then
    echo ""
    echo "==> No Wireguard config — setting up openconnect (PolyQuartz)..."

    if ! command -v openconnect > /dev/null 2>&1; then
        sudo apt-get update -qq > /dev/null 2>&1 || true
        sudo apt-get install -qq -y openconnect > /dev/null 2>&1 || {
            echo "   WARNING: openconnect installation had issues"
        }
        echo "   ✓ openconnect installed"
    else
        echo "   ✓ openconnect already available"
    fi

    # Register vpnconnect in .bashrc (idempotent, same fence pattern as steps 5/5b)
    VPN_HOOK_BEGIN="# >>> neuropoly vpnconnect >>>"
    VPN_HOOK_END="# <<< neuropoly vpnconnect <<<"
    VPN_CONNECT_SCRIPT="/workspaces/neuropoly-db/.devcontainer/connect-vpn.sh"

    if ! grep -Fq "$VPN_HOOK_BEGIN" "$USER_BASHRC"; then
        {
            echo ""
            echo "$VPN_HOOK_BEGIN"
            echo "# Disable venv's own PS1 modification so we control the entire prompt here."
            echo "VIRTUAL_ENV_DISABLE_PROMPT=1"
            echo "export VIRTUAL_ENV_DISABLE_PROMPT"
            echo "vpnconnect() { bash \"$VPN_CONNECT_SCRIPT\"; }"
            echo "export -f vpnconnect"
            echo "vpndisconnect() {"
            echo "  local _pid_file=/tmp/vpn.pid"
            echo "  local _user_file=/tmp/.vpn_connected_user"
            echo "  if [[ -f \"\$_pid_file\" ]] && [[ -d \"/proc/\$(cat \"\$_pid_file\" 2>/dev/null)\" ]]; then"
            echo "    sudo kill \"\$(cat \"\$_pid_file\")\""
            echo "    sudo rm -f \"\$_pid_file\" \"\$_user_file\""
            echo "    echo 'VPN disconnected.'"
            echo "  else"
            echo "    echo 'VPN is not connected.'"
            echo "    sudo rm -f \"\$_pid_file\" \"\$_user_file\""
            echo "  fi"
            echo "  __vpn_prompt"
            echo "}"
            echo "export -f vpndisconnect"
            echo "# VPN prompt indicator."
            echo "# Always reconstructs PS1 from _VPN_BASE_PS1 so [VPN] always appears before"
            echo "# (.venv), regardless of which was activated first."
            echo "# VIRTUAL_ENV_DISABLE_PROMPT=1 (above) prevents venv/activate from prepending"
            echo "# (.venv) itself; we render it here instead."
            echo "_VPN_PS1_PREFIX=\$'\\001\\033[1m\\002\\001\\033[38;5;214m\\002[VPN]\\001\\033[0m\\002 '"
            echo "_VPN_BASE_PS1=\"\${_VPN_BASE_PS1:-\$PS1}\""
            echo "__vpn_prompt() {"
            echo "  local _ps1=\"\${_VPN_BASE_PS1}\""
            echo "  if [[ -n \"\${VIRTUAL_ENV}\" ]]; then"
            echo "    _ps1=\"(\${VIRTUAL_ENV_PROMPT:-\${VIRTUAL_ENV##*/}}) \${_ps1}\""
            echo "  fi"
            echo "  if [[ -f /tmp/vpn.pid ]] && [[ -d \"/proc/\$(cat /tmp/vpn.pid 2>/dev/null)\" ]]; then"
            echo "    _ps1=\"\${_VPN_PS1_PREFIX}\${_ps1}\""
            echo "  fi"
            echo "  PS1=\"\${_ps1}\""
            echo "}"
            echo "if [[ \"\${PROMPT_COMMAND}\" != *__vpn_prompt* ]]; then"
            echo "  PROMPT_COMMAND=\"\${PROMPT_COMMAND:+\${PROMPT_COMMAND}; }__vpn_prompt\""
            echo "fi"
            echo "$VPN_HOOK_END"
        } >> "$USER_BASHRC"
        echo "   Added vpnconnect / vpndisconnect functions to $USER_BASHRC"
    else
        echo "   vpnconnect / vpndisconnect functions already present in $USER_BASHRC"
    fi
fi

# ── 8. git-annex ───────────────────────────────────────────────────────────────

echo ""
echo "==> Checking git-annex availability..."
if ! command -v git-annex > /dev/null 2>&1; then
    sudo apt-get update -qq > /dev/null 2>&1 || true
    sudo apt-get install -qq -y git-annex > /dev/null 2>&1 || {
        echo "   WARNING: git-annex installation had issues (may still work)"
    }
    echo "   ✓ git-annex installed"
else
    echo "   ✓ git-annex already available"
fi

echo ""
echo "==> Checking native BIDS tools..."
command -v dcm2niix >/dev/null || {
    echo "ERROR: dcm2niix is missing. Rebuild the workspace container."
    exit 1
}
bash scripts/install_bids_validator.sh .venv

# ── 9. Summary ────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────────────────────"
echo " ✅  Setup complete"
echo ""
echo "   Python  : $(python --version)"
echo "   Kernel  : Python (neuropoly-db)"
echo ""
echo "   Open a new VS Code terminal to view the endpoint header."
echo "──────────────────────────────────────────────────────────"
