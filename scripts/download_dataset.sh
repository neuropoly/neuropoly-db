#!/usr/bin/env bash
# =============================================================================
# Download ALL BIDS example datasets for the course
#
# Performs a shallow clone of the bids-examples repository and moves every
# directory that contains a dataset_description.json AND at least one NIfTI
# file into data/.  Non-dataset dirs (docs, tools) are skipped automatically.
#
# Result: ~80 datasets, ~4 800 NIfTI files, < 60 MB on disk (all dummy data).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/../data"
REPO_URL="https://github.com/bids-standard/bids-examples.git"
TMP_DIR="${DATA_DIR}/_bids_examples_tmp"

echo "==> Downloading BIDS example datasets..."

# If data/ already has datasets, offer a quick summary and exit
existing=$(find "${DATA_DIR}" -maxdepth 2 -name "dataset_description.json" 2>/dev/null | wc -l)
if [ "${existing}" -gt 10 ]; then
    total_nifti=$(find "${DATA_DIR}" -name "*.nii.gz" -o -name "*.nii" 2>/dev/null | wc -l)
    echo "    ${existing} datasets already present (${total_nifti} NIfTI files)."
    echo "    To re-download, remove data/ and run again."
    exit 0
fi

mkdir -p "${DATA_DIR}"
cd "${DATA_DIR}"

# Shallow clone the full repository (~60 MB)
echo "    Cloning bids-examples (shallow)..."
rm -rf "${TMP_DIR}"
git clone --depth 1 "${REPO_URL}" "${TMP_DIR}" 2>&1 | tail -1

# Move valid BIDS datasets into data/
moved=0
for d in "${TMP_DIR}"/*/; do
    name=$(basename "${d}")
    # Skip repo-level non-dataset dirs
    case "${name}" in docs|tools|_*) continue ;; esac
    # Must have dataset_description.json
    [ -f "${d}/dataset_description.json" ] || continue
    # Must have at least one NIfTI file
    nifti=$(find "${d}" \( -name "*.nii.gz" -o -name "*.nii" \) 2>/dev/null | wc -l)
    [ "${nifti}" -gt 0 ] || continue
    # Skip if already present
    [ -d "${DATA_DIR}/${name}" ] && continue
    mv "${d}" "${DATA_DIR}/${name}"
    moved=$((moved + 1))
done

rm -rf "${TMP_DIR}"

total_ds=$(find "${DATA_DIR}" -maxdepth 2 -name "dataset_description.json" | wc -l)
total_nifti=$(find "${DATA_DIR}" -name "*.nii.gz" -o -name "*.nii" 2>/dev/null | wc -l)

echo ""
echo "==> Done."
echo "    Moved ${moved} new dataset(s)."
echo "    Total: ${total_ds} datasets, ${total_nifti} NIfTI files"
echo "    Disk usage: $(du -sh "${DATA_DIR}" | cut -f1)"
