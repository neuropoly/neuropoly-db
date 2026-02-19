#!/usr/bin/env bash
# =============================================================================
# Download a small public BIDS dataset for the course
# Uses bids-examples/ds001 from GitHub (lightweight, ~10 subjects)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/../data"

REPO_URL="https://github.com/bids-standard/bids-examples.git"
DATASET="ds001"

echo "==> Downloading BIDS example dataset: ${DATASET}"
echo "    Target directory: ${DATA_DIR}/${DATASET}"

if [ -d "${DATA_DIR}/${DATASET}" ]; then
    echo "    Dataset already exists. Skipping download."
    echo "    To re-download, remove ${DATA_DIR}/${DATASET} and run again."
    exit 0
fi

# Sparse checkout — only clone the one dataset we need
mkdir -p "${DATA_DIR}"
cd "${DATA_DIR}"

git clone --depth 1 --filter=blob:none --sparse "${REPO_URL}" _bids_examples_tmp
cd _bids_examples_tmp
git sparse-checkout set "${DATASET}"

# Move the dataset out and clean up
mv "${DATASET}" "${DATA_DIR}/${DATASET}"
cd "${DATA_DIR}"
rm -rf _bids_examples_tmp

echo "==> Done. Dataset available at: ${DATA_DIR}/${DATASET}"
echo "    Contents:"
ls -la "${DATA_DIR}/${DATASET}/"
