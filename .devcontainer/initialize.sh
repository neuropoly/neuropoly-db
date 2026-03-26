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

set_env_var() {
  local env_file="$1"
  local key="$2"
  local value="$3"

  if grep -Eq "^[[:space:]]*${key}=" "$env_file"; then
    sed -i -E "s|^[[:space:]]*${key}=.*|${key}=${value}|" "$env_file"
  elif grep -Eq "^[[:space:]]*#[[:space:]]*${key}=" "$env_file"; then
    sed -i -E "s|^[[:space:]]*#[[:space:]]*${key}=.*|${key}=${value}|" "$env_file"
  else
    echo "${key}=${value}" >> "$env_file"
  fi
}


# Ensure submodules are available before compose resolves include files.
git submodule update --init

# Ensure Neurobagel local runtime config files exist before compose up.
ENV_TEMPLATE="$NB_DIR/template.env"
ENV_FILE="$NB_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
  [ -f "$ENV_TEMPLATE" ] || error_exit "Missing required template: $ENV_TEMPLATE"

  ensure_template_marker "$ENV_TEMPLATE" "COMPOSE_PROJECT_NAME=neurobagel_node"
  ensure_template_marker "$ENV_TEMPLATE" "NB_GRAPH_USERNAME=DBUSER"
  ensure_template_marker "$ENV_TEMPLATE" "NB_GRAPH_DB=repositories/my_db"
  ensure_template_marker "$ENV_TEMPLATE" "LOCAL_GRAPH_DATA=./data"
  ensure_template_marker "$ENV_TEMPLATE" "# NB_API_QUERY_URL=http://localhost:8080"
  ensure_template_marker "$ENV_TEMPLATE" "COMPOSE_PROFILES=node"

  cp "$ENV_TEMPLATE" "$ENV_FILE"

  set_env_var "$ENV_FILE" "COMPOSE_PROJECT_NAME" "neuropoly_nb_dev"
  set_env_var "$ENV_FILE" "NB_GRAPH_USERNAME" "dbuser"
  set_env_var "$ENV_FILE" "NB_GRAPH_DB" "repositories/my_db"
  set_env_var "$ENV_FILE" "LOCAL_GRAPH_DATA" "./data"
  set_env_var "$ENV_FILE" "NB_GRAPH_PORT_HOST" "7200"
  set_env_var "$ENV_FILE" "NB_RETURN_AGG" "true"
  set_env_var "$ENV_FILE" "NB_NAPI_PORT_HOST" "8000"
  set_env_var "$ENV_FILE" "NB_FAPI_PORT_HOST" "8080"
  set_env_var "$ENV_FILE" "NB_FEDERATE_REMOTE_PUBLIC_NODES" "False"
  set_env_var "$ENV_FILE" "NB_QUERY_PORT_HOST" "3000"
  set_env_var "$ENV_FILE" "NB_API_QUERY_URL" "/fapi"
  set_env_var "$ENV_FILE" "NB_ENABLE_AUTH" "false"
  set_env_var "$ENV_FILE" "COMPOSE_PROFILES" "node"

  echo "Created neurobagel-recipes/.env from template.env"
else
  echo "Using existing neurobagel-recipes/.env"

  # Ensure key local-dev values are consistent even for pre-existing files.
  set_env_var "$ENV_FILE" "NB_FEDERATE_REMOTE_PUBLIC_NODES" "False"
  set_env_var "$ENV_FILE" "NB_API_QUERY_URL" "/fapi"

  # Warn about settings that commonly cause "stuck" query UI behavior.
  NB_REMOTE_NODES_RAW="$(grep -E '^[[:space:]]*NB_FEDERATE_REMOTE_PUBLIC_NODES=' "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
  NB_API_QUERY_URL_RAW="$(grep -E '^[[:space:]]*NB_API_QUERY_URL=' "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"

  if [ -z "$NB_REMOTE_NODES_RAW" ] || [[ "$NB_REMOTE_NODES_RAW" =~ ^([Tt]rue|1|yes|YES)$ ]]; then
    echo "WARNING: NB_FEDERATE_REMOTE_PUBLIC_NODES is enabled or unset in neurobagel-recipes/.env."
    echo "         In devcontainer this can make query endpoints very slow/time out."
    echo "         Recommended value for local dev: NB_FEDERATE_REMOTE_PUBLIC_NODES=False"
  fi

  if [ "$NB_API_QUERY_URL_RAW" = "http://federation:8000" ] || [ "$NB_API_QUERY_URL_RAW" = "http://federation:8000/" ]; then
    echo "WARNING: NB_API_QUERY_URL points to internal service DNS (federation:8000)."
    echo "         Browser clients should use gateway path: NB_API_QUERY_URL=/fapi"
  fi
fi

NODES_TEMPLATE="$NB_DIR/local_nb_nodes.template.json"
NODES_FILE="$NB_DIR/local_nb_nodes.json"

if [ ! -f "$NODES_FILE" ]; then
  [ -f "$NODES_TEMPLATE" ] || error_exit "Missing required template: $NODES_TEMPLATE"

  ensure_template_marker "$NODES_TEMPLATE" '"NodeName"'
  ensure_template_marker "$NODES_TEMPLATE" '"ApiURL"'

  cp "$NODES_TEMPLATE" "$NODES_FILE"
  sed -i -E '0,/"ApiURL"[[:space:]]*:[[:space:]]*"[^"]*"/s//"ApiURL": "http:\/\/api:8000"/' "$NODES_FILE"

  if ! grep -Fq '"ApiURL": "http://api:8000"' "$NODES_FILE"; then
    error_exit "Failed to set ApiURL in $NODES_FILE. Upstream template likely changed; update .devcontainer/initialize.sh generation logic."
  fi

  echo "Created neurobagel-recipes/local_nb_nodes.json from local_nb_nodes.template.json"
else
  echo "Using existing neurobagel-recipes/local_nb_nodes.json"
fi
