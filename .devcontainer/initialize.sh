#!/bin/bash
# .devcontainer/initialize.sh — runs on host before containers are created
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
NB_DIR="$ROOT_DIR/neurobagel-recipes"

cd "$ROOT_DIR"

error_exit() {
  echo "ERROR: $1" >&2
  exit 1
}

ensure_template_marker() {
  local template_file="$1"
  local marker="$2"
  if ! grep -Fq "$marker" "$template_file"; then
    error_exit "Template validation failed for $template_file (missing marker: $marker). Upstream template likely changed; update .devcontainer/initialize.sh generation logic."
  fi
}

# Ensure submodule files needed for compose inheritance are present.
if [ ! -f "$NB_DIR/docker-compose.yml" ]; then
  error_exit "Missing neurobagel-recipes files at $NB_DIR. Initialize submodules before launching the devcontainer."
fi

# Ensure local_nb_nodes.json exists (needed by federation service).
NODES_TEMPLATE="$NB_DIR/local_nb_nodes.template.json"
NODES_FILE="$ROOT_DIR/local_nb_nodes.json"

if [ ! -f "$NODES_FILE" ]; then
  [ -f "$NODES_TEMPLATE" ] || error_exit "Missing required template: $NODES_TEMPLATE"

  ensure_template_marker "$NODES_TEMPLATE" '"NodeName"'
  ensure_template_marker "$NODES_TEMPLATE" '"ApiURL"'

  cp "$NODES_TEMPLATE" "$NODES_FILE"
  sed -i -E '0,/"ApiURL"[[:space:]]*:[[:space:]]*"[^"]*"/s//"ApiURL": "http:\/\/api:8000"/' "$NODES_FILE"

  if ! grep -Fq '"ApiURL": "http://api:8000"' "$NODES_FILE"; then
    error_exit "Failed to set ApiURL in $NODES_FILE. Upstream template likely changed; update .devcontainer/initialize.sh generation logic."
  fi

  echo "Created repository-root local_nb_nodes.json from neurobagel-recipes/local_nb_nodes.template.json"
else
  echo "Using existing repository-root local_nb_nodes.json"
fi
