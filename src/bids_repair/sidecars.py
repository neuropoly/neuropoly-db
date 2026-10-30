"""BIDS tabular-sidecar construction from ordinary column metadata."""

from copy import deepcopy
from typing import Any

BIDS_VALID_SIDECAR_FIELDS = {
    "LongName",
    "Description",
    "Format",
    "Levels",
    "Units",
    "TermURL",
    "HED",
    "Maximum",
    "Minimum",
    "Delimiter",
}

DEFAULT_COLUMN_METADATA = {
    "participant_id": {"LongName": "Participant ID"},
    "session_id": {"LongName": "Session ID"},
    "sub_id": {"LongName": "Participant ID"},
    "age": {"LongName": "Age", "Units": "year"},
    "sex": {"LongName": "Sex"},
    "diagnosis": {"LongName": "Diagnosis"},
}


def validate_bids_sidecar(
    sidecar_dict: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    cleaned: dict[str, Any] = {}
    warnings = []
    for column, entry in sidecar_dict.items():
        if not isinstance(entry, dict):
            cleaned[column] = entry
            continue
        for field in entry:
            if field not in BIDS_VALID_SIDECAR_FIELDS:
                warnings.append(
                    f"Stripped non-BIDS field '{field}' from column '{column}'"
                )
        cleaned[column] = {
            key: value
            for key, value in entry.items()
            if key in BIDS_VALID_SIDECAR_FIELDS
        }

    return cleaned, warnings


def build_participants_sidecar(
    columns: list[str],
    metadata: dict[str, dict[str, Any]],
    existing: dict[str, Any] | None = None,
    *,
    preserve_extensions: bool = False,
) -> dict[str, Any]:
    sidecar = {}
    for column in columns:
        entry = deepcopy((existing or {}).get(column, {}))
        if not isinstance(entry, dict):
            raise ValueError(f"Sidecar entry for '{column}' must be an object")

        for key, value in DEFAULT_COLUMN_METADATA.get(column, {}).items():
            entry.setdefault(key, value)
        entry.update(deepcopy(metadata.get(column, {})))
        entry.setdefault("Description", f"Column: {column}")
        sidecar[column] = entry

    if not preserve_extensions:
        sidecar, _ = validate_bids_sidecar(sidecar)
    return sidecar
