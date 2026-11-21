#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/query_neurobagel.sh [options]

Options:
  --api-url URL       Neurobagel node/federation API base URL
  --sex TERM          Sex SNOMED/controlled term, e.g. snomed:248153007
  --min-age NUMBER    Minimum age
  --max-age NUMBER    Maximum age
  --output FILE       Output TSV (default: query-results.tsv)
  -h, --help          Show this message
USAGE
}

API_URL="${NB_API_URL:-https://api.neurobagel.org}"
SEX=""
MIN_AGE=""
MAX_AGE=""
OUTPUT="query-results.tsv"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-url) API_URL="$2"; shift 2 ;;
    --sex) SEX="$2"; shift 2 ;;
    --min-age) MIN_AGE="$2"; shift 2 ;;
    --max-age) MAX_AGE="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

API_URL="${API_URL%/}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
DATASETS_JSON="$TMP_DIR/datasets.json"
SUBJECTS_JSON="$TMP_DIR/subjects.json"
QUERY_JSON="$TMP_DIR/query.json"

python3 - "$QUERY_JSON" "$MIN_AGE" "$MAX_AGE" "$SEX" <<'PY'
import json
import sys

path, min_age, max_age, sex = sys.argv[1:]
query = {}
if min_age:
    query["min_age"] = float(min_age)
if max_age:
    query["max_age"] = float(max_age)
if sex:
    query["sex"] = sex

with open(path, "w", encoding="utf-8") as f:
    json.dump(query, f)
PY

# Neurobagel current API exposes these operations as POST requests.
if ! curl --fail-with-body --silent --show-error --retry 3 --retry-all-errors \
    --connect-timeout 10 --max-time 120 -L --post301 \
    -X POST -H 'accept: application/json' -H 'content-type: application/json' \
    --data-binary "@${QUERY_JSON}" \
    "${API_URL}/datasets" > "$DATASETS_JSON"; then
  echo "Neurobagel dataset query failed: ${API_URL}/datasets" >&2
  exit 1
fi

if ! curl --fail-with-body --silent --show-error --retry 3 --retry-all-errors \
    --connect-timeout 10 --max-time 300 -L --post301 \
    -X POST -H 'accept: application/json' -H 'content-type: application/json' \
    --data-binary "@${QUERY_JSON}" \
    "${API_URL}/subjects" > "$SUBJECTS_JSON"; then
  echo "Neurobagel subject query failed: ${API_URL}/subjects" >&2
  exit 1
fi

python3 - "$DATASETS_JSON" "$SUBJECTS_JSON" "$OUTPUT" <<'PY'
import csv
import json
import sys

datasets_path, subjects_path, output_path = sys.argv[1:]

with open(datasets_path, encoding="utf-8") as f:
    datasets = json.load(f)
with open(subjects_path, encoding="utf-8") as f:
    subject_groups = json.load(f)

if not isinstance(datasets, list) or not isinstance(subject_groups, list):
    raise SystemExit("Unexpected Neurobagel API response: expected JSON arrays")

by_uuid = {str(d.get("dataset_uuid", "")): d for d in datasets}
header = [
    "DatasetName", "RepositoryURL", "NumMatchingSubjects", "SubjectID",
    "SessionID", "ImagingSessionPath", "SessionType",
    "NumMatchingPhenotypicSessions", "NumMatchingImagingSessions",
    "Age", "Sex", "Diagnosis", "Assessment", "SessionImagingModalities",
    "SessionCompletedPipelines", "DatasetImagingModalities",
    "DatasetPipelines", "AccessLink",
]
rows = []

for group in subject_groups:
    dataset_uuid = str(group.get("dataset_uuid", ""))
    dataset = by_uuid.get(dataset_uuid, {})
    subject_data = group.get("subject_data", [])
    if isinstance(subject_data, str):
        try:
            subject_data = json.loads(subject_data)
        except json.JSONDecodeError:
            subject_data = []
    if not isinstance(subject_data, list):
        continue

    dataset_name = dataset.get("dataset_name") or dataset_uuid
    repository_url = dataset.get("repository_url") or ""
    access_link = dataset.get("access_link") or ""

    for session in subject_data:
        if not isinstance(session, dict):
            continue
        age = session.get("age")
        sex = session.get("sex")
        diagnoses = session.get("diagnosis") or []
        assessments = session.get("assessment") or []
        modalities = session.get("image_modal") or []

        def label_list(values):
            if not isinstance(values, list):
                return str(values or "")
            labels = []
            for value in values:
                if isinstance(value, dict):
                    labels.append(str(value.get("label") or value.get("name") or value.get("term") or ""))
                elif value is not None:
                    labels.append(str(value))
            return ";".join(v for v in labels if v)

        rows.append({
            "DatasetName": dataset_name,
            "RepositoryURL": repository_url,
            "NumMatchingSubjects": dataset.get("num_matching_subjects") or "",
            "SubjectID": session.get("sub_id") or "",
            "SessionID": session.get("session_id") or "",
            "ImagingSessionPath": session.get("session_file_path") or "",
            "SessionType": session.get("session_type") or "",
            "NumMatchingPhenotypicSessions": session.get("num_matching_phenotypic_sessions") or 0,
            "NumMatchingImagingSessions": session.get("num_matching_imaging_sessions") or 0,
            "Age": "" if age is None else age,
            "Sex": "" if sex is None else sex,
            "Diagnosis": label_list(diagnoses),
            "Assessment": label_list(assessments),
            "SessionImagingModalities": label_list(modalities),
            "SessionCompletedPipelines": json.dumps(session.get("completed_pipelines") or {}, sort_keys=True),
            "DatasetImagingModalities": ";".join(dataset.get("image_modals") or []),
            "DatasetPipelines": json.dumps(dataset.get("available_pipelines") or {}, sort_keys=True),
            "AccessLink": access_link,
        })

with open(output_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=header, delimiter="\t", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} subject/session row(s) to {output_path}")
PY
