#!/bin/bash
set -e
export NB_GRAPH_PASSWORD=$(cat /run/secrets/db_user_password)

datasets_metadata_path="${NB_DATASETS_METADATA_PATH:-/data/datasets_metadata.json}"
wait_timeout_seconds="${NB_DATASETS_METADATA_WAIT_TIMEOUT:-300}"
wait_interval_seconds="${NB_DATASETS_METADATA_WAIT_INTERVAL:-2}"

echo "[neuropoly-api] waiting for datasets metadata at ${datasets_metadata_path}"
elapsed_seconds=0
while [[ ! -f "${datasets_metadata_path}" ]]; do
	if [[ "${elapsed_seconds}" -ge "${wait_timeout_seconds}" ]]; then
		echo "[neuropoly-api] timed out waiting for ${datasets_metadata_path}" >&2
		exit 1
	fi

	sleep "${wait_interval_seconds}"
	elapsed_seconds=$((elapsed_seconds + wait_interval_seconds))
done

echo "[neuropoly-api] found datasets metadata at ${datasets_metadata_path}"

# Inject vocab patch before uvicorn starts
python3 /usr/src/neurobagel/inject_vocab_patch.py

exec uvicorn app.main:app --proxy-headers --host 0.0.0.0 --port ${NB_API_PORT:-8000}
