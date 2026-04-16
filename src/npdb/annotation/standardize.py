"""
BIDS standardization functions for participants.tsv and participants.json.

Provides:
- Header renaming based on resolved mappings
- Missing standard column insertion
- BIDS-compliant participants.json generation
- BIDS sidecar validation (strip non-BIDS fields)
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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


def load_header_map(path: Path) -> Dict[str, List[str]]:
    """
    Load a header translation map from a JSON file.

    Expected format::

        {
          "desired_name": ["variant1", "variant2"],
          "other_name": ["variant3"]
        }

    Args:
        path: Path to the JSON file.

    Returns:
        Dict mapping desired output header names to lists of input variants.

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
                f"Header map keys must be strings, got: {type(key).__name__}")
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(
                f"Header map values must be lists of strings, invalid value for key '{key}'"
            )

    return data


def validate_header_map_keys(header_map: Dict[str, List[str]], valid_keys: Set[str]) -> None:
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


def apply_header_map(
    tsv_path: Path,
    header_map: Dict[str, List[str]],
    dry_run: bool = False,
) -> Dict[str, str]:
    """
    Rename TSV column headers using a user-supplied translation map.

    For each TSV column, checks (case-insensitive) whether it appears in any
    key's variant list.  Matching columns are renamed to the key.

    This runs as a **prior stage** before the resolver/matcher, so the
    resolver sees already-clean header names.

    Args:
        tsv_path: Path to participants.tsv.
        header_map: Mapping of ``{desired_name: [variant1, variant2, ...]}``.
        dry_run: If True, print changes without writing.

    Returns:
        Dict mapping ``old_header -> new_header`` for columns that were renamed.

    Raises:
        ValueError: On ambiguous mapping (column matches multiple keys) or
            conflicting mapping (multiple columns map to the same key).
    """
    # Build reverse lookup: lowered variant -> desired key
    variant_to_key: Dict[str, str] = {}
    for desired, variants in header_map.items():
        for v in variants:
            low = v.lower()
            if low in variant_to_key and variant_to_key[low] != desired:
                raise ValueError(
                    f"Ambiguous header map: variant '{v}' appears under both "
                    f"'{variant_to_key[low]}' and '{desired}'"
                )
            variant_to_key[low] = desired

    # Read TSV headers
    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if not lines:
        return {}

    headers = lines[0].rstrip("\n").split("\t")

    # Match headers against variants
    rename_map: Dict[str, str] = {}
    # desired_key -> original column that claimed it
    seen_targets: Dict[str, str] = {}

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


def rename_tsv_headers(
    tsv_path: Path,
    resolved_mappings: List[ResolvedMapping],
    dry_run: bool = False,
) -> Dict[str, str]:
    """
    Rename TSV column headers based on resolved mappings.

    For each column, if the resolver mapped it to a known mapping key,
    the header is renamed to that canonical key name.

    Args:
        tsv_path: Path to participants.tsv.
        resolved_mappings: List of ResolvedMapping from MappingResolver.
        dry_run: If True, print changes without writing.

    Returns:
        Dict mapping old_header -> new_header for columns that were renamed.
    """
    rename_map: Dict[str, str] = {}

    # Build rename map from resolved mappings
    for mapping in resolved_mappings:
        if mapping.source == "unresolved":
            continue
        # The canonical name is the key in phenotype_mappings.json that matched.
        # For static matches, column_name IS the key; for fuzzy, we need the matched key.
        # The mapping_data comes from the matched key's entry.
        # Extract the canonical key from the rationale for fuzzy matches.
        canonical = _extract_canonical_name(mapping)
        if canonical and canonical != mapping.column_name:
            rename_map[mapping.column_name] = canonical

    if not rename_map:
        return rename_map

    if dry_run:
        print("Dry-run: header renames that would be applied:")
        for old, new in rename_map.items():
            print(f"  {old} → {new}")
        return rename_map

    # Read entire TSV
    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if not lines:
        return rename_map

    # Rename headers
    headers = lines[0].rstrip("\n").split("\t")
    new_headers = [rename_map.get(h, h) for h in headers]
    lines[0] = "\t".join(new_headers) + "\n"

    with open(tsv_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return rename_map


def _extract_canonical_name(mapping: ResolvedMapping) -> Optional[str]:
    """
    Extract the canonical mapping key name from a ResolvedMapping.

    For static matches the column_name is already canonical.
    For deterministic (fuzzy) matches the rationale contains the matched key.
    """
    if mapping.source == "static":
        # Static match means column_name is already a key in phenotype_mappings
        return mapping.column_name
    if mapping.source == "deterministic":
        # Rationale format: "Fuzzy match: 'col' → 'canonical_key' (...)"
        rationale = mapping.rationale
        arrow_idx = rationale.find("→")
        if arrow_idx != -1:
            rest = rationale[arrow_idx + 1:].strip()
            # Extract the quoted key after the arrow
            if rest.startswith("'"):
                end = rest.find("'", 1)
                if end != -1:
                    return rest[1:end]
    return None


def add_missing_standard_columns(
    tsv_path: Path,
    mappings_registry: Dict[str, Any],
    dry_run: bool = False,
) -> List[str]:
    """
    Add missing standard columns (from phenotype_mappings) to participants.tsv.

    Compares post-rename columns against all keys in mappings_registry["mappings"]
    and appends missing columns filled with 'n/a'.

    Args:
        tsv_path: Path to participants.tsv (already renamed).
        mappings_registry: Full phenotype_mappings dict (with "mappings" key).
        dry_run: If True, print which columns would be added without writing.

    Returns:
        List of column names that were added.
    """
    standard_columns = list(mappings_registry.get("mappings", {}).keys())

    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if not lines:
        return []

    existing = lines[0].rstrip("\n").split("\t")
    missing = [col for col in standard_columns if col not in existing]

    if not missing:
        return []

    if dry_run:
        print("Dry-run: standard columns that would be added:")
        for col in missing:
            print(f"  + {col} (filled with 'n/a')")
        return missing

    # Append missing columns
    new_lines = []
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if i == 0:
            # Header row
            stripped += "\t" + "\t".join(missing)
        else:
            # Data rows — fill with n/a
            stripped += "\t" + "\t".join(["n/a"] * len(missing))
        new_lines.append(stripped + "\n")

    with open(tsv_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return missing


def generate_participants_json(
    tsv_path: Path,
    resolved_mappings: List[ResolvedMapping],
    phenotype_mappings: Dict[str, Any],
    existing_json_path: Optional[Path] = None,
    keep_annotations: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
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

    Returns:
        The generated sidecar dict.
    """
    mappings_dict = phenotype_mappings.get("mappings", {})

    # Build a lookup from column name to its ResolvedMapping data
    # This handles columns that were fuzzy-matched (mapping_data comes from
    # the matched canonical key, not the original column name).
    resolved_by_col = {m.column_name: m for m in resolved_mappings}

    # Read current column names from TSV
    with open(tsv_path, "r", encoding="utf-8") as f:
        current_columns = f.readline().rstrip("\n").split("\t")

    # Load existing JSON if present
    existing: Dict[str, Any] = {}
    if existing_json_path and existing_json_path.exists():
        with open(existing_json_path, "r", encoding="utf-8") as f:
            existing = json.load(f)

    sidecar: Dict[str, Any] = {}

    for col in current_columns:
        entry: Dict[str, Any] = {}

        # Start with existing entry if present (preserve user content)
        if col in existing:
            entry = dict(existing[col])

        # Find mapping data for this column:
        # 1. Direct lookup by (post-rename) column name in phenotype_mappings
        # 2. Fallback to mapping_data carried by the ResolvedMapping (handles
        #    fuzzy matches and pre-rename column names)
        mapping_data = mappings_dict.get(col, {})
        if not mapping_data and col in resolved_by_col:
            mapping_data = resolved_by_col[col].mapping_data

        # LongName
        if col in _VARIABLE_LONG_NAMES:
            entry["LongName"] = _VARIABLE_LONG_NAMES[col]

        # Description
        if "Description" not in entry:
            entry["Description"] = mapping_data.get(
                "note", f"Column: {col}"
            )

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
            bids_levels: Dict[str, Any] = {}
            for value, info in levels_data.items():
                if isinstance(info, dict):
                    level_entry: Dict[str, str] = {}
                    if "label" in info:
                        level_entry["Description"] = info["label"]
                    if "termURL" in info:
                        level_entry["TermURL"] = expand_iri(info["termURL"])
                    bids_levels[value] = level_entry if level_entry else value
                else:
                    bids_levels[value] = str(info)
            entry["Levels"] = bids_levels

        # Neurobagel Annotations block (opt-in only)
        if keep_annotations and mapping_data:
            annotations_block: Dict[str, Any] = {}
            if variable_iri:
                annotations_block["IsAbout"] = {
                    "TermURL": variable_iri,
                    "Label": _VARIABLE_LONG_NAMES.get(col, col),
                }
            if variable_type:
                annotations_block["VariableType"] = variable_type
            annotations_block["MissingValues"] = []
            if levels_data:
                nb_levels: Dict[str, Any] = {}
                for value, info in levels_data.items():
                    if isinstance(info, dict):
                        nb_levels[value] = {
                            "TermURL": info.get("termURL", ""),
                            "Label": info.get("label", value),
                        }
                nb_levels_final = nb_levels if nb_levels else None
                if nb_levels_final:
                    annotations_block["Levels"] = nb_levels_final
            fmt = mapping_data.get("format")
            if fmt:
                annotations_block["Format"] = {
                    "TermURL": fmt,
                    "Label": fmt,
                }
            entry["Annotations"] = annotations_block

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
    sidecar_dict: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Validate a participants.json sidecar against BIDS spec.

    Strips any fields not in the BIDS-valid set. Returns the cleaned dict
    and a list of warnings for each stripped field.

    Args:
        sidecar_dict: The sidecar dict to validate.

    Returns:
        Tuple of (cleaned_dict, list of warning strings).
    """
    cleaned: Dict[str, Any] = {}
    warnings: List[str] = []

    for col_name, col_entry in sidecar_dict.items():
        if not isinstance(col_entry, dict):
            cleaned[col_name] = col_entry
            continue

        clean_entry: Dict[str, Any] = {}
        for field, value in col_entry.items():
            if field in BIDS_VALID_SIDECAR_FIELDS:
                clean_entry[field] = value
            else:
                warnings.append(
                    f"Stripped non-BIDS field '{field}' from column '{col_name}'"
                )
        cleaned[col_name] = clean_entry

    return cleaned, warnings
