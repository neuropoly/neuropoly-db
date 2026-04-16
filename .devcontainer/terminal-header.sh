#!/usr/bin/env bash
# Printed in every new interactive VS Code terminal via ~/.bashrc hook.

[[ $- != *i* ]] && return 0

# Avoid accidental double-printing if sourced multiple times.
if [[ -n "${NEUROPOLY_TERMINAL_HEADER_SHOWN:-}" ]]; then
  return 0
fi
export NEUROPOLY_TERMINAL_HEADER_SHOWN=1

PUBLIC_PROXY_URL="(open proxy:80 from VS Code Ports)"
if [[ -n "${VSCODE_PROXY_URI:-}" ]]; then
  PUBLIC_PROXY_URL="${VSCODE_PROXY_URI//\{\{port\}\}/80}"
fi

cat <<EOF

======================================================================
 NeuroPoly Devcontainer - Neurobagel Endpoints
======================================================================
 Browser entrypoint (via VS Code port forwarding):
   ${PUBLIC_PROXY_URL}

 Internal service DNS (inside containers only):
   http://api:8000
   http://federation:8000
   http://query_federation:5173
   http://graph:7200

 Notes:
 - The proxy routes API paths (/nodes, /query, /diagnoses, etc.)
   to the federation API; everything else serves the query UI.
 - Use VS Code Ports panel to forward proxy:80 and graph:7200.
======================================================================

EOF
