#!/bin/bash
set -e
export NB_GRAPH_PASSWORD=$(cat /run/secrets/db_user_password)

# Inject vocab patch before uvicorn starts
python3 /usr/src/neurobagel/inject_vocab_patch.py

exec uvicorn app.main:app --proxy-headers --host 0.0.0.0 --port ${NB_API_PORT:-8000}
