"""Neurobagel policy adapters for the provider-independent BIDS table API."""

import json
from pathlib import Path
from typing import Any

from bids_repair.sidecars import (
    BIDS_VALID_SIDECAR_FIELDS,
    DEFAULT_COLUMN_METADATA,
    build_participants_sidecar,
    validate_bids_sidecar,
)
from bids_repair.tables import add_columns, apply_header_map, load_header_map
from bids_repair.tables import normalize_participants_tsv as normalize_bids_table
from bids_repair.tables import rename_columns
from bids_repair.utils import parse_tsv_columns
from npdb.annotation.autofix import (
    auto_add_missing_value_sentinels,
    dedup_participant_ids,
    fill_empty_id_rows,
    fix_age_format,
    fix_missing_levels,
    fix_single_column_tsv,
    fix_trailing_empty_tsv_cells,
    strip_tsv_cell_whitespace,
)
from npdb.automation.mappings.resolvers import ResolvedMapping
from npdb.external.neurobagel.schema import expand_iri

_VARIABLE_TYPE_TO_FORMAT = {
    "Continuous": "number",
    "Identifier": "string",
    "Categorical": "string",
}
_VARIABLE_LONG_NAMES = {
    column: metadata["LongName"] for column, metadata in DEFAULT_COLUMN_METADATA.items()
}


def normalize_participants_tsv(
    tsv_path: Path, annotations_path: Path | None = None, *, dry_run: bool = False
) -> list[str]:
    warnings = normalize_bids_table(tsv_path, dry_run=dry_run)
    if annotations_path is not None and annotations_path.exists() and not dry_run:
        for cleanup in (
            fix_age_format,
            auto_add_missing_value_sentinels,
            fix_missing_levels,
        ):
            warnings.extend(cleanup(tsv_path, annotations_path))
    return warnings


def validate_header_map_keys(
    header_map: dict[str, dict[str, Any]], valid_keys: set[str]
) -> None:
    invalid = sorted(set(header_map) - valid_keys)
    if invalid:
        raise ValueError(
            f"Invalid header map keys: {invalid}. Allowed keys: {sorted(valid_keys)}"
        )


def header_map_variables(header_map: dict[str, dict[str, Any]]) -> set[str]:
    return {entry["variable"] for entry in header_map.values() if "variable" in entry}


def rename_tsv_headers(
    tsv_path: Path,
    resolved_mappings: list[ResolvedMapping],
    dry_run: bool = False,
    protected_columns: set[str] | None = None,
) -> dict[str, str]:
    renames = {
        mapping.column_name: mapping.canonical_key
        for mapping in resolved_mappings
        if mapping.source != "unresolved"
        and mapping.column_name not in (protected_columns or set())
        and mapping.canonical_key
        and mapping.canonical_key != mapping.column_name
    }
    if renames:
        rename_columns(tsv_path, renames, dry_run=dry_run)
    return renames


def add_missing_standard_columns(
    tsv_path: Path,
    mappings_registry: dict[str, Any],
    dry_run: bool = False,
    extra_covered_variables: set[str] | None = None,
) -> list[str]:
    mappings = mappings_registry.get("mappings", {})
    existing = parse_tsv_columns(tsv_path)
    covered = set(extra_covered_variables or set())
    covered.update(
        mappings[column]["variable"]
        for column in existing
        if mappings.get(column, {}).get("variable")
    )

    columns = [
        column
        for column, entry in mappings.items()
        if not entry.get("variable") or entry["variable"] not in covered
    ]
    return add_columns(tsv_path, columns, dry_run=dry_run)


def _resolve_column_mapping_data(
    col: str,
    mappings_dict: dict[str, Any],
    resolved_by_col: dict[str, Any],
    header_map: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    data = mappings_dict.get(col, {}) or {}
    if not data and col in resolved_by_col:
        data = resolved_by_col[col].mapping_data or {}
    if not data and header_map and col in header_map:
        variable = header_map[col].get("variable")
        if variable:
            data = next(
                (
                    entry
                    for entry in mappings_dict.values()
                    if isinstance(entry, dict) and entry.get("variable") == variable
                ),
                {},
            )
    return data


def _build_bids_levels(levels_data: dict[str, Any]) -> dict[str, Any]:
    levels = {}
    for value, info in levels_data.items():
        if isinstance(info, dict):
            entry = {}
            if "label" in info:
                entry["Description"] = info["label"]
            if "termURL" in info:
                entry["TermURL"] = expand_iri(info["termURL"])
            levels[value] = entry or value
        else:
            levels[value] = str(info)
    return levels


def _build_nb_annotations_block(
    col: str,
    mapping_data: dict[str, Any],
    variable_iri: str,
    variable_type: str | None,
    levels_data: dict[str, Any] | None,
) -> dict[str, Any]:
    block: dict[str, Any] = {}
    if variable_iri:
        block["IsAbout"] = {
            "TermURL": variable_iri,
            "Label": _VARIABLE_LONG_NAMES.get(col, col),
        }
    if variable_type:
        block["VariableType"] = variable_type
    block["MissingValues"] = []
    if levels_data:
        levels = {
            value: {
                "TermURL": info.get("termURL", ""),
                "Label": info.get("label", value),
            }
            for value, info in levels_data.items()
            if isinstance(info, dict)
        }
        if levels:
            block["Levels"] = levels
    if mapping_data.get("format"):
        block["Format"] = {
            "TermURL": mapping_data["format"],
            "Label": mapping_data["format"],
        }
    return block


def generate_participants_json(
    tsv_path: Path,
    resolved_mappings: list[ResolvedMapping],
    phenotype_mappings: dict[str, Any],
    existing_json_path: Path | None = None,
    keep_annotations: bool = False,
    dry_run: bool = False,
    column_names: list[str] | None = None,
    header_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    columns = (
        list(column_names) if column_names is not None else parse_tsv_columns(tsv_path)
    )
    existing = (
        json.loads(existing_json_path.read_text(encoding="utf-8"))
        if existing_json_path and existing_json_path.exists()
        else {}
    )

    resolved = {mapping.column_name: mapping for mapping in resolved_mappings}
    metadata = {}
    for column in columns:
        data = _resolve_column_mapping_data(
            column, phenotype_mappings.get("mappings", {}), resolved, header_map
        )
        entry = dict(existing.get(column, {}))
        if column in _VARIABLE_LONG_NAMES:
            entry["LongName"] = _VARIABLE_LONG_NAMES[column]
        entry.setdefault("Description", data.get("note", f"Column: {column}"))
        variable_type = data.get("variableType")
        if variable_type in _VARIABLE_TYPE_TO_FORMAT:
            entry.setdefault("Format", _VARIABLE_TYPE_TO_FORMAT[variable_type])
        variable = data.get("variable", "")
        if variable:
            entry["TermURL"] = expand_iri(variable)
        if data.get("levels"):
            entry["Levels"] = _build_bids_levels(data["levels"])
        if keep_annotations and data:
            entry["Annotations"] = _build_nb_annotations_block(
                column, data, variable, variable_type, data.get("levels")
            )
        metadata[column] = entry

    sidecar = build_participants_sidecar(
        columns, metadata, preserve_extensions=keep_annotations
    )
    if dry_run:
        print("Dry-run: participants.json that would be generated:")
        print(json.dumps(sidecar, indent=2))
    return sidecar
