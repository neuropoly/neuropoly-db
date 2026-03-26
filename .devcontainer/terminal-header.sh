#!/usr/bin/env bash
# Printed in every new interactive VS Code terminal via ~/.bashrc hook.

[[ $- != *i* ]] && return 0

# Avoid accidental double-printing if sourced multiple times.
if [[ -n "${NEUROPOLY_TERMINAL_HEADER_SHOWN:-}" ]]; then
  return 0
fi
export NEUROPOLY_TERMINAL_HEADER_SHOWN=1

PUBLIC_GATEWAY_URL="(open gateway:80 from VS Code Ports)"
if [[ -n "${VSCODE_PROXY_URI:-}" ]]; then
  PUBLIC_GATEWAY_URL="${VSCODE_PROXY_URI//\{\{port\}\}/80}"
fi

cat <<EOF

======================================================================
 NeuroPoly Devcontainer - Neurobagel Endpoints
======================================================================
 Browser entrypoint (recommended):
   ${PUBLIC_GATEWAY_URL}
   ${PUBLIC_GATEWAY_URL}/fapi
   ${PUBLIC_GATEWAY_URL}/napi

 Internal service DNS (inside containers only):
   http://gateway
   http://api:8000
   http://federation:8000
   http://query_federation:5173
   http://graph:7200

 Notes:
 - Use VS Code Ports for gateway:80 in tunnel scenarios.
 - Do not rely on fixed localhost ports in layered remote setups.
======================================================================

EOF
