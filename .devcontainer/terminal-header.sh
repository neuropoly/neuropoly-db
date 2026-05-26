#!/usr/bin/env bash
# Printed in every new interactive VS Code terminal via ~/.bashrc hook.

[[ $- != *i* ]] && return 0

# Avoid accidental double-printing if sourced multiple times.
if [[ -n "${NEUROPOLY_TERMINAL_HEADER_SHOWN:-}" ]]; then
  return 0
fi
export NEUROPOLY_TERMINAL_HEADER_SHOWN=1

# ---------------------------------------------------------------------------
# Resolve live host-bound ports from the running compose stack.
# Falls back to the default values defined in docker-compose.yml / .env.
# NOTE: This workspace container runs with network_mode: host, so it shares
#       the Docker host's network namespace.  Docker bridge DNS names
#       (api, graph, …) are NOT reachable here — use localhost:PORT instead.
# ---------------------------------------------------------------------------
_proxy_port=$(docker compose port proxy 80 2>/dev/null | cut -d: -f2)
_proxy_port=${_proxy_port:-9000}
_graph_port=$(docker compose port graph 7200 2>/dev/null | cut -d: -f2)
_graph_port=${_graph_port:-7200}
_api_port=$(docker compose port api 8000 2>/dev/null | cut -d: -f2)
_api_port=${_api_port:-8000}
_fapi_port=$(docker compose port federation 8000 2>/dev/null | cut -d: -f2)
_fapi_port=${_fapi_port:-8080}

# ---------------------------------------------------------------------------
# VS Code forwarded URL.  Set automatically by both:
#   - VS Code Remote - SSH  (SSH port forwarding over the SSH connection)
#   - VS Code Tunnel        (dev-tunnel HTTPS URL)
# ---------------------------------------------------------------------------
if [[ -n "${VSCODE_PROXY_URI:-}" ]]; then
  _vscode_proxy="${VSCODE_PROXY_URI//\{\{port\}\}/${_proxy_port}}"
  _vscode_section="\
   Proxy  (UI + APIs)   ${_vscode_proxy}
   GraphDB admin        see VS Code Ports panel → forward  graph:7200"
else
  _vscode_section="\
   Open the VS Code Ports panel and forward : proxy:80"
fi

cat <<EOF

======================================================================
 NeuroPoly Devcontainer — Neurobagel Access Guide
======================================================================

 ── In this devcontainer terminal ───────────────────────────────────
   Proxy  (UI + APIs)   http://localhost:${_proxy_port}
   GraphDB admin        http://localhost:${_graph_port}
   Node API  (direct)   http://localhost:${_api_port}
   Federation (direct)  http://localhost:${_fapi_port}

 ── Via VS Code Ports panel (Remote-SSH or Tunnel) ──────────────────
${_vscode_section}

 ── From the host machine running Docker ────────────────────────────
   Proxy  (UI + APIs)   http://localhost:${_proxy_port}
   GraphDB admin        http://localhost:${_graph_port}

 ── From a remote machine connecting to the Docker host ─────────────
${_vscode_section}
======================================================================

EOF
