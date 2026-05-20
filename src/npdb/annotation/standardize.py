"""
BIDS standardization functions for participants.tsv and participants.json.

Provides:
- Header renaming based on resolved mappings
- Missing standard column insertion
- BIDS-compliant participants.json generation
- BIDS sidecar validation (strip non-BIDS fields)
- Age format auto-detection and correction
- Missing categorical value sentinel injection
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from npdb.annotation.autofix import (
    _NA_PATTERNS,
    _AGE_FORMAT_PATTERNS,
    _AGE_NONSTANDARD_PATTERNS,
    _detect_age_format,
    _get_categorical_terms,
    _is_plain_float,
    auto_add_missing_value_sentinels,
    dedup_participant_ids,
    fill_empty_id_rows,
    fix_age_format,
    fix_missing_levels,
    fix_single_column_tsv,
    load_categorical_terms,
)
from npdb.automation.mappings.resolvers import ResolvedMapping
from npdb.external.neurobagel.schema import expand_iri

# BIDS-valid fields for tabular file sidecar entries (from BIDS common-principles spec)
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

# Mapping from phenotype_mappings.json variableType to BIDS Format string
_VARIABLE_TYPE_TO_FORMAT = {
    "Continuous": "number",
    "Identifier": "string",
    "Categorical": "string",
}

# Human-readable long names for known variables
_VARIABLE_LONG_NAMES = {
    "participant_id": "Participant ID",
    "session_id": "Session ID",
    "age": "Age",
    "sex": "Sex",
    "diagnosis": "Diagnosis",
    "sub_id": "Participant ID",
}

# Units for known continuous variables
_VARIABLE_UNITS = {
    "age": "year",
}


def load_header_map(path: Path) -> dict[str, dict[str, Any]]:
    """
    Load a header translation map from a JSON file.

    Expected format::

        {
          "desired_name": {
            "aliases": ["variant1", "variant2"],
            "variable": "nb:SomeVariable"   // optional
          }
        }

    Args:
        path: Path to the JSON file.

    Returns:
        Dict mapping desired output header names to entry dicts containing
        at least ``"aliases"`` (list of strings) and optionally ``"variable"``.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the JSON structure is invalid.
    """
    if not path.exists():
        raise FileNotFoundError(f"Header map file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Header map must be a JSON object (dict)")

    for key, value in data.items():
        if not isinstance(key, str):
            raise ValueError(
                f"Header map keys must be strings, got: {type(key).__name__}"
            )
        if not isinstance(value, dict):
            raise ValueError(
                f"Header map values must be objects, invalid value for key '{key}'"
            )
        aliases = value.get("aliases")
        if not isinstance(aliases, list) or not all(
            isinstance(v, str) for v in aliases
        ):
            raise ValueError(f"'aliases' must be a list of strings for key '{key}'")
        variable = value.get("variable")
        if variable is not None and not isinstance(variable, str):
            raise ValueError(f"'variable' must be a string for key '{key}'")

    return data


def validate_header_map_keys(
    header_map: dict[str, dict[str, Any]], valid_keys: set[str]
) -> None:
    """
    Validate that all keys in a header map belong to a set of valid keys.

    Args:
        header_map: The header translation map.
        valid_keys: Set of allowed key names.

    Raises:
        ValueError: If any key is not in valid_keys, listing all invalid keys.
    """
    invalid = sorted(set(header_map.keys()) - valid_keys)
    if invalid:
        raise ValueError(
            f"Invalid header map keys: {invalid}. "
            f"Allowed keys: {sorted(valid_keys)}"
        )


def header_map_variables(header_map: dict[str, dict[str, Any]]) -> set[str]:
    """
    Extract the set of Neurobagel variable IRIs declared in a header map.

    Args:
        header_map: The header translation map.

    Returns:
        Set of variable IRI strings (entries without ``"variable"`` are skipped).
    """
    return {entry["variable"] for entry in header_map.values() if "variable" in entry}


def apply_header_map(
    tsv_path: Path,
    header_map: dict[str, dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, str]:
    """
    Rename TSV column headers using a user-supplied translation map.

    For each TSV column, checks (case-insensitive) whether it appears in any
    key's aliases list.  Matching columns are renamed to the key.

    This runs as a **prior stage** before the resolver/matcher, so the
    resolver sees already-clean header names.

    Args:
        tsv_path: Path to participants.tsv.
        header_map: Mapping of ``{desired_name: {"aliases": [...], ...}}``.
        dry_run: If True, print changes without writing.

    Returns:
        Dict mapping ``old_header -> new_header`` for columns that were renamed.

    Raises:
        ValueError: On ambiguous mapping (column matches multiple keys) or
            conflicting mapping (multiple columns map to the same key).
    """
    # Build reverse lookup: lowered variant -> desired key
    variant_to_key: dict[str, str] = {}
    for desired, entry in header_map.items():
        for v in entry["aliases"]:
            low = v.lower()
            if low in variant_to_key and variant_to_key[low] != desired:
                raise ValueError(
                    f"Ambiguous header map: variant '{v}' appears under both "
                    f"'{variant_to_key[low]}' and '{desired}'"
                )
            variant_to_key[low] = desired

    lines = _read_tsv_lines(tsv_path)
    if not lines:
        return {}

    headers = lines[0].rstrip("\n").split("\t")

    # Match headers against variants
    rename_map: dict[str, str] = {}
    # desired_key -> original column that claimed it
    seen_targets: dict[str, str] = {}

    for h in headers:
        target = variant_to_key.get(h.lower())
        if target is None:
            continue
        if target in seen_targets:
            raise ValueError(
                f"Conflicting header map: columns '{seen_targets[target]}' and "
                f"'{h}' both map to '{target}'"
            )
        seen_targets[target] = h
        if h != target:
            rename_map[h] = target

    if not rename_map:
        return rename_map

    if dry_run:
        print("Dry-run: header-map renames that would be applied:")
        for old, new in rename_map.items():
            print(f"  {old} → {new}")
        return rename_map

    # Apply renames
    new_headers = [rename_map.get(h, h) for h in headers]
    lines[0] = "\t".join(new_headers) + "\n"

    with open(tsv_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return rename_map


def _read_tsv_lines(path: Path) -> list[str]:
    """Read all lines from *path* as-is (preserving line endings)."""
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def _write_tsv_lines(path: Path, lines: list[str]) -> None:
    """Write *lines* to *path*, replacing its content."""
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _apply_rename_to_tsv(tsv_path: Path, rename_map: dict[str, str]) -> None:
    """Rewrite the header row of *tsv_path* in-place using *rename_map* (old->new)."""
    lines = _read_tsv_lines(tsv_path)
    if not lines:
        return
    headers = lines[0].rstrip("\n").split("\t")
    lines[0] = "\t".join(rename_map.get(h, h) for h in headers) + "\n"
    _write_tsv_lines(tsv_path, lines)


def rename_tsv_headers(
    tsv_path: Path,
    resolved_mappings: list[ResolvedMapping],
    dry_run: bool = False,
    protected_columns: set[str | None] = None,
) -> dict[str, str]:
    """
    Rename TSV column headers based on resolved mappings.

    For each column, if the resolver mapped it to a known mapping key,
    the header is renamed to that canonical key name.

    Args:
        tsv_path: Path to participants.tsv.
        resolved_mappings: List of ResolvedMapping from MappingResolver.
        dry_run: If True, print changes without writing.
        protected_columns: Column names that must not be renamed (e.g.
            header-map keys that the user explicitly chose).

    Returns:
        Dict mapping old_header -> new_header for columns that were renamed.
    """
    rename_map: dict[str, str] = {}
    protected = protected_columns or set()

    # Build rename map from resolved mappings
    for mapping in resolved_mappings:
        if mapping.source == "unresolved":
            continue
        if mapping.column_name in protected:
            continue
        # The canonical name is the key in phenotype_mappings.json that matched.
        canonical = mapping.canonical_key
        if canonical and canonical != mapping.column_name:
            rename_map[mapping.column_name] = canonical

    if not rename_map:
        return rename_map

    if dry_run:
        print("Dry-run: header renames that would be applied:")
        for old, new in rename_map.items():
            print(f"  {old} → {new}")
        return rename_map

    _apply_rename_to_tsv(tsv_path, rename_map)
    return rename_map


def add_missing_standard_columns(
    tsv_path: Path,
    mappings_registry: dict[str, Any],
    dry_run: bool = False,
    extra_covered_variables: set[str | None] = None,
) -> list[str]:
    """
    Add missing standard columns (from phenotype_mappings) to participants.tsv.

    Compares post-rename columns against all keys in mappings_registry["mappings"]
    and appends missing columns filled with 'n/a'.

    A standard column is considered *already covered* if any existing column
    maps to the same ``variable`` (e.g. ``nb:ParticipantID``).  This prevents
    adding ``sub_id`` when ``participant_id`` is already present.  However,
    if the input data already contains multiple columns for the same variable
    they are left untouched (duplicate handling is deferred to annotation modes).

    Variables listed in *extra_covered_variables* (e.g. from a header-map) are
    also treated as covered.

    Args:
        tsv_path: Path to participants.tsv (already renamed).
        mappings_registry: Full phenotype_mappings dict (with "mappings" key).
        dry_run: If True, print which columns would be added without writing.
        extra_covered_variables: Additional Neurobagel variable IRIs that should
            be considered already present (e.g. declared in a header-map).

    Returns:
        List of column names that were added.
    """
    all_mappings = mappings_registry.get("mappings", {})
    standard_columns = list(all_mappings.keys())

    lines = _read_tsv_lines(tsv_path)
    if not lines:
        return []

    existing = lines[0].rstrip("\n").split("\t")

    # Build set of variables already covered by existing columns
    covered_variables: set[str] = set(extra_covered_variables or set())
    for col in existing:
        col_mapping = all_mappings.get(col)
        if col_mapping:
            var = col_mapping.get("variable")
            if var:
                covered_variables.add(var)

    # A standard column is missing only if:
    # 1. It is not already present by name, AND
    # 2. Its variable is not already covered by another existing column
    missing = []
    for col in standard_columns:
        if col in existing:
            continue
        col_var = all_mappings[col].get("variable", "")
        if col_var and col_var in covered_variables:
            continue
        missing.append(col)

    if not missing:
        return []

    if dry_run:
        print("Dry-run: standard columns that would be added:")
        for col in missing:
            print(f"  + {col} (filled with 'n/a')")
        return missing

    # Append missing columns
    suffix = "\t" + "\t".join(missing)
    na_suffix = "\t" + "\t".join(["n/a"] * len(missing))
    header_row, *data_rows = lines
    new_lines = [header_row.rstrip("\n") + suffix + "\n"] + [
        row.rstrip("\n") + na_suffix + "\n" for row in data_rows
    ]
    _write_tsv_lines(tsv_path, new_lines)

    return missing


def _resolve_column_mapping_data(
    col: str,
    mappings_dict: dict[str, Any],
    resolved_by_col: dict,
    header_map: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Three-step lookup for the phenotype mapping entry of *col*."""
    mapping_data = mappings_dict.get(col, {})
    if not mapping_data and col in resolved_by_col:
        mapping_data = resolved_by_col[col].mapping_data
    if not mapping_data and header_map and col in header_map:
        hm_var = header_map[col].get("variable")
        if hm_var:
            for _mdata in mappings_dict.values():
                if _mdata.get("variable") == hm_var:
                    mapping_data = _mdata
                    break
    return mapping_data


def _build_bids_levels(levels_data: dict[str, Any]) -> dict[str, Any]:
    """Build the BIDS ``Levels`` block from a phenotype_mappings levels entry."""
    bids_levels: dict[str, Any] = {}
    for value, info in levels_data.items():
        if isinstance(info, dict):
            level_entry: dict[str, str] = {}
            if "label" in info:
                level_entry["Description"] = info["label"]
            if "termURL" in info:
                level_entry["TermURL"] = expand_iri(info["termURL"])
            bids_levels[value] = level_entry if level_entry else value
        else:
            bids_levels[value] = str(info)
    return bids_levels


def _build_nb_annotations_block(
    col: str,
    mapping_data: dict[str, Any],
    variable_iri: str,
    variable_type: str | None,
    levels_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the NeuroBagel ``Annotations`` block for a single column entry."""
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
        nb_levels = {
            value: {
                "TermURL": info.get("termURL", "") if isinstance(info, dict) else "",
                "Label": info.get("label", value) if isinstance(info, dict) else value,
            }
            for value, info in levels_data.items()
            if isinstance(info, dict)
        }
        if nb_levels:
            block["Levels"] = nb_levels
    fmt = mapping_data.get("format")
    if fmt:
        block["Format"] = {"TermURL": fmt, "Label": fmt}
    return block


def generate_participants_json(
    tsv_path: Path,
    resolved_mappings: list[ResolvedMapping],
    phenotype_mappings: dict[str, Any],
    existing_json_path: Path | None = None,
    keep_annotations: bool = False,
    dry_run: bool = False,
    column_names: list[str | None] = None,
    header_map: dict[str, dict[str, Any | None]] = None,
) -> dict[str, Any]:
    """
    Generate a BIDS-compliant participants.json sidecar.

    Builds sidecar entries from resolved mappings, using phenotype_mappings
    for metadata (levels, format, etc.). Merges with existing JSON if present.
    Validates output against BIDS sidecar spec.

    Args:
        tsv_path: Path to participants.tsv (used to read current column names).
        resolved_mappings: Resolved column mappings.
        phenotype_mappings: Full phenotype_mappings dict.
        existing_json_path: Optional path to existing participants.json to merge.
        keep_annotations: If True, include Neurobagel Annotations block.
        dry_run: If True, print JSON to terminal without writing.
        column_names: If provided, use these column names instead of reading
            from tsv_path (useful in dry-run when TSV has not been modified).
        header_map: Optional header translation map.  When a column is a
            header-map key with a ``"variable"`` entry, the variable's
            phenotype mapping data is used for sidecar metadata.

    Returns:
        The generated sidecar dict.
    """
    mappings_dict = phenotype_mappings.get("mappings", {})

    # Build a lookup from column name to its ResolvedMapping data
    # This handles columns that were fuzzy-matched (mapping_data comes from
    # the matched canonical key, not the original column name).
    resolved_by_col = {m.column_name: m for m in resolved_mappings}

    # Read current column names from TSV, or use provided override
    if column_names is not None:
        current_columns = list(column_names)
    else:
        with open(tsv_path, "r", encoding="utf-8") as f:
            current_columns = f.readline().rstrip("\n").split("\t")

    # Load existing JSON if present
    existing: dict[str, Any] = {}
    if existing_json_path and existing_json_path.exists():
        with open(existing_json_path, "r", encoding="utf-8") as f:
            existing = json.load(f)

    sidecar: dict[str, Any] = {}

    for col in current_columns:
        entry: dict[str, Any] = dict(existing.get(col, {}))

        mapping_data = _resolve_column_mapping_data(
            col, mappings_dict, resolved_by_col, header_map
        )

        # LongName
        if col in _VARIABLE_LONG_NAMES:
            entry["LongName"] = _VARIABLE_LONG_NAMES[col]

        # Description
        if "Description" not in entry:
            entry["Description"] = mapping_data.get("note", f"Column: {col}")

        # Format (BIDS string)
        variable_type = mapping_data.get("variableType")
        if variable_type and variable_type in _VARIABLE_TYPE_TO_FORMAT:
            if "Format" not in entry:
                entry["Format"] = _VARIABLE_TYPE_TO_FORMAT[variable_type]

        # Units (for continuous variables)
        if col in _VARIABLE_UNITS:
            if "Units" not in entry:
                entry["Units"] = _VARIABLE_UNITS[col]

        # TermURL — expand to full URL
        variable_iri = mapping_data.get("variable", "")
        if variable_iri:
            entry["TermURL"] = expand_iri(variable_iri)

        # Levels (for categorical variables)
        levels_data = mapping_data.get("levels")
        if levels_data:
            entry["Levels"] = _build_bids_levels(levels_data)

        # Neurobagel Annotations block (opt-in only)
        if keep_annotations and mapping_data:
            entry["Annotations"] = _build_nb_annotations_block(
                col, mapping_data, variable_iri, variable_type, levels_data
            )

        sidecar[col] = entry

    # Validate: strip non-BIDS fields unless keep_annotations
    if not keep_annotations:
        sidecar, _ = validate_bids_sidecar(sidecar)

    if dry_run:
        print("Dry-run: participants.json that would be generated:")
        print(json.dumps(sidecar, indent=2))
        return sidecar

    return sidecar


def validate_bids_sidecar(
    sidecar_dict: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Validate a participants.json sidecar against BIDS spec.

    Strips any fields not in the BIDS-valid set. Returns the cleaned dict
    and a list of warnings for each stripped field.

    Args:
        sidecar_dict: The sidecar dict to validate.

    Returns:
        Tuple of (cleaned_dict, list of warning strings).
    """
    cleaned: dict[str, Any] = {}
    warnings: list[str] = []

    for col_name, col_entry in sidecar_dict.items():
        if not isinstance(col_entry, dict):
            cleaned[col_name] = col_entry
            continue

        stripped = [f for f in col_entry if f not in BIDS_VALID_SIDECAR_FIELDS]
        for field in stripped:
            warnings.append(
                f"Stripped non-BIDS field '{field}' from column '{col_name}'"
            )
        cleaned[col_name] = {
            k: v for k, v in col_entry.items() if k in BIDS_VALID_SIDECAR_FIELDS
        }

    return cleaned, warnings
