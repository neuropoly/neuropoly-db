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

import csv
import json
import re
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


def load_header_map(path: Path) -> Dict[str, Dict[str, Any]]:
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
                f"Header map keys must be strings, got: {type(key).__name__}")
        if not isinstance(value, dict):
            raise ValueError(
                f"Header map values must be objects, invalid value for key '{key}'"
            )
        aliases = value.get("aliases")
        if not isinstance(aliases, list) or not all(isinstance(v, str) for v in aliases):
            raise ValueError(
                f"'aliases' must be a list of strings for key '{key}'"
            )
        variable = value.get("variable")
        if variable is not None and not isinstance(variable, str):
            raise ValueError(
                f"'variable' must be a string for key '{key}'"
            )

    return data


def validate_header_map_keys(header_map: Dict[str, Dict[str, Any]], valid_keys: Set[str]) -> None:
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


def header_map_variables(header_map: Dict[str, Dict[str, Any]]) -> Set[str]:
    """
    Extract the set of Neurobagel variable IRIs declared in a header map.

    Args:
        header_map: The header translation map.

    Returns:
        Set of variable IRI strings (entries without ``"variable"`` are skipped).
    """
    return {
        entry["variable"]
        for entry in header_map.values()
        if "variable" in entry
    }


def apply_header_map(
    tsv_path: Path,
    header_map: Dict[str, Dict[str, Any]],
    dry_run: bool = False,
) -> Dict[str, str]:
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
    variant_to_key: Dict[str, str] = {}
    for desired, entry in header_map.items():
        for v in entry["aliases"]:
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
    protected_columns: Optional[Set[str]] = None,
) -> Dict[str, str]:
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
    rename_map: Dict[str, str] = {}
    protected = protected_columns or set()

    # Build rename map from resolved mappings
    for mapping in resolved_mappings:
        if mapping.source == "unresolved":
            continue
        if mapping.column_name in protected:
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
    extra_covered_variables: Optional[Set[str]] = None,
) -> List[str]:
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

    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if not lines:
        return []

    existing = lines[0].rstrip("\n").split("\t")

    # Build set of variables already covered by existing columns
    covered_variables: Set[str] = set(extra_covered_variables or set())
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
    column_names: Optional[List[str]] = None,
    header_map: Optional[Dict[str, Dict[str, Any]]] = None,
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
        # 3. If column is a header-map key with a declared variable, look up
        #    the first phenotype_mappings entry sharing that variable
        mapping_data = mappings_dict.get(col, {})
        if not mapping_data and col in resolved_by_col:
            mapping_data = resolved_by_col[col].mapping_data
        if not mapping_data and header_map and col in header_map:
            hm_var = header_map[col].get("variable")
            if hm_var:
                for _mkey, _mdata in mappings_dict.items():
                    if _mdata.get("variable") == hm_var:
                        mapping_data = _mdata
                        break

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


# ---------------------------------------------------------------------------
# NA-like sentinel patterns recognised by NeuroBagel
# ---------------------------------------------------------------------------

_NA_PATTERNS: Set[str] = {"-", "n/a", "na", "N/A", "NA", "", "unknown", "?"}

# ---------------------------------------------------------------------------
# Categorical terms config loader
# ---------------------------------------------------------------------------


def load_categorical_terms(
    path: Path,
) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
    """Load and validate ``config/categorical_terms.json``.

    Returns a tuple of two flat dicts:

    * ``alias_to_preferred`` — every alias **and** the preferred key itself
      maps to the preferred key (e.g. ``{"hc": "hc", "control": "hc", ...}``).
    * ``preferred_to_term`` — preferred key → ``{"TermURL": ..., "Label": ...}``.

    Raises :exc:`ValueError` on malformed entries (missing ``TermURL`` or
    ``Label`` keys, or non-list ``aliases``).
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = json.load(fh)

    alias_to_preferred: Dict[str, str] = {}
    preferred_to_term: Dict[str, Dict[str, str]] = {}

    for preferred, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValueError(
                f"categorical_terms.json: entry for {preferred!r} must be a dict"
            )
        for required_key in ("TermURL", "Label", "aliases"):
            if required_key not in entry:
                raise ValueError(
                    f"categorical_terms.json: entry for {preferred!r} is missing"
                    f" required key {required_key!r}"
                )
        if not isinstance(entry["aliases"], list):
            raise ValueError(
                f"categorical_terms.json: 'aliases' for {preferred!r} must be a list"
            )

        preferred_to_term[preferred] = {
            "TermURL": entry["TermURL"],
            "Label": entry["Label"],
        }
        # The preferred key itself is also a valid lookup key
        alias_to_preferred[preferred] = preferred
        for alias in entry["aliases"]:
            alias_to_preferred[alias] = preferred

    return alias_to_preferred, preferred_to_term


_CATEGORICAL_TERMS_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "categorical_terms.json"
)
_ALIAS_TO_PREFERRED, _PREFERRED_TO_TERM = load_categorical_terms(
    _CATEGORICAL_TERMS_PATH)

# Age format detection — ordered list of (neurobagel_term, pattern) tuples.
# Multiple patterns may map to the same term; the first match wins per value.
# Standard notations come first so they take priority in majority voting.
_AGE_FORMAT_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # Standard / canonical notations
    ("nb:FromBounded", re.compile(r"^\d+(\.\d+)?\+$")),           # 89+
    ("nb:FromRange",   re.compile(r"^\d+(\.\d+)?-\d+(\.\d+)?$")),  # 18-25
    ("nb:FromISO8601", re.compile(r"^P\d")),                       # P30Y
    ("nb:FromEuro",    re.compile(r"^\d+,\d+$")),                  # 42,5
    # Non-standard but accepted single-ended notations — all map to FromBounded
    ("nb:FromBounded", re.compile(r"^\+\d+(\.\d+)?$")),            # +89
    ("nb:FromBounded", re.compile(r"^\d+(\.\d+)?-$")),             # 42-
    ("nb:FromBounded", re.compile(r"^-\d+(\.\d+)?$")),             # -42
]

# Subset used only to emit soft warnings (non-standard but accepted forms)
_AGE_NONSTANDARD_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("nb:FromBounded", re.compile(r"^\+\d+(\.\d+)?$")),            # +89
    ("nb:FromBounded", re.compile(r"^\d+(\.\d+)?-$")),             # 42-
    ("nb:FromBounded", re.compile(r"^-\d+(\.\d+)?$")),             # -42
]


def _detect_age_format(values: List[str]) -> str:
    """
    Detect the NeuroBagel age format from a list of non-missing age values.

    Checks each pattern in ``_AGE_FORMAT_PATTERNS`` in order and picks the
    term that matches the most values.  Falls back to ``nb:FromFloat``.
    """
    non_empty = [v for v in values if v.strip()]
    if not non_empty:
        return "nb:FromFloat"
    counts: Dict[str, int] = {}
    for v in non_empty:
        v = v.strip()
        for term, pat in _AGE_FORMAT_PATTERNS:
            if pat.match(v):
                counts[term] = counts.get(term, 0) + 1
                break
        else:
            counts["nb:FromFloat"] = counts.get("nb:FromFloat", 0) + 1

    return max(counts, key=lambda k: counts[k])


def _is_plain_float(value: str) -> bool:
    """Return True if *value* (stripped) parses as a plain float or int."""
    try:
        float(value.strip())
        return True
    except ValueError:
        return False


def fix_age_format(tsv_path: Path, annotations_path: Path) -> List[str]:
    """
    Detect the age encoding in *tsv_path* and update *annotations_path*.

    Each age value is first classified as **parseable** (matches one of the
    known format regexes or is a plain float/int) or **unparseable** (anything
    else, including ``"-"``, ``"n/a"``, ``"unknown"``, free-text strings, etc.).

    * **Parseable values** drive format detection via majority vote.  The
      dominant format is written to ``Format.TermURL`` when it differs from the
      current annotation.
    * **Unparseable values** are added to the ``MissingValues`` list in the
      annotation (so Bagel's ``is_missing_value()`` skips them) and normalized
      to ``"n/a"`` in the TSV in-place (the canonical BIDS missing-value
      sentinel).

    When *no* parseable values are found (the entire column is NA-like), the
    existing ``Format.TermURL`` is preserved unchanged — there is no basis for
    detection — but the MissingValues update and TSV normalization still occur.

    Returns a list of warning strings.  Both the annotations file and the TSV
    may be modified in-place.
    """
    warnings: List[str] = []

    if not annotations_path.exists():
        return warnings

    with open(annotations_path, "r", encoding="utf-8") as fh:
        annotations = json.load(fh)

    # Find the age column — the one whose IsAbout.TermURL == "nb:Age"
    age_col: Optional[str] = None
    for col, col_data in annotations.items():
        ann = col_data.get("Annotations", {})
        if ann.get("IsAbout", {}).get("TermURL") == "nb:Age":
            age_col = col
            break

    if age_col is None:
        return warnings

    if not tsv_path.exists():
        return warnings

    age_values: List[str] = []
    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if age_col not in (reader.fieldnames or []):
            return warnings
        for row in reader:
            age_values.append(row.get(age_col, ""))

    if not age_values:
        return warnings

    # Classify: parseable (format regex match or plain float) vs unparseable
    parseable: List[str] = []
    unparseable: List[str] = []
    for v in age_values:
        if any(pat.match(v.strip()) for _, pat in _AGE_FORMAT_PATTERNS) or _is_plain_float(v):
            parseable.append(v)
        else:
            unparseable.append(v)

    # Diagnostic saturation warning (informational only)
    if age_values and len(unparseable) / len(age_values) >= 0.9:
        warnings.append(
            f"Age column '{age_col}': ≥90 % of values are missing/NA "
            f"({len(unparseable)}/{len(age_values)}); age data may be incomplete."
        )

    ann_block = annotations[age_col].get("Annotations", {})
    annotations_changed = False

    # ── Format detection ──────────────────────────────────────────────────
    if parseable:
        detected = _detect_age_format(parseable)
        current_fmt = ann_block.get("Format", {}).get(
            "TermURL", "nb:FromFloat")
        if current_fmt != detected:
            ann_block.setdefault("Format", {})
            ann_block["Format"]["TermURL"] = detected
            ann_block["Format"]["Label"] = detected
            annotations[age_col]["Annotations"] = ann_block
            annotations_changed = True
            warnings.append(
                f"Age column '{age_col}': updated Format.TermURL from "
                f"'{current_fmt}' to '{detected}' (auto-detected from data)."
            )

        # Warn (non-breaking) about non-standard single-ended notations
        nonstandard = [
            v for v in parseable
            if any(pat.match(v.strip()) for _, pat in _AGE_NONSTANDARD_PATTERNS)
        ]
        if nonstandard:
            examples = ", ".join(repr(e) for e in nonstandard[:3])
            suffix = " …" if len(nonstandard) > 3 else ""
            warnings.append(
                f"Age column '{age_col}': non-standard age notation detected "
                f"({examples}{suffix}). Accepted and mapped to '{detected}'. "
                f"Consider normalising to canonical form (e.g. '89+' instead of "
                f"'+89', '42+' instead of '42-' or '-42')."
            )
    else:
        # No parseable values — preserve existing Format, emit informational warning
        warnings.append(
            f"Age column '{age_col}': no parseable age values found; "
            f"Format kept unchanged (all values will be treated as missing)."
        )

    # ── MissingValues update ──────────────────────────────────────────────
    if unparseable:
        existing_mv: List[str] = list(ann_block.get("MissingValues", []))
        added_to_mv: List[str] = []
        # Deduplicated, insertion-ordered list of unique unparseable values
        for v in dict.fromkeys(unparseable):
            if v not in existing_mv:
                existing_mv.append(v)
                added_to_mv.append(v)
        # Ensure "n/a" (the canonical form we write back to the TSV) is present
        if "n/a" not in existing_mv:
            existing_mv.append("n/a")
        ann_block["MissingValues"] = existing_mv
        annotations[age_col]["Annotations"] = ann_block
        annotations_changed = True
        if added_to_mv:
            warnings.append(
                f"Age column '{age_col}': added {len(added_to_mv)} unparseable "
                f"value(s) to MissingValues: {added_to_mv!r} (auto-fix)."
            )

    if annotations_changed:
        with open(annotations_path, "w", encoding="utf-8") as fh:
            json.dump(annotations, fh, indent=2)

    # ── TSV normalization ─────────────────────────────────────────────────
    if unparseable:
        unparseable_set = set(unparseable)
        with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            fieldnames = reader.fieldnames or []
            rows = list(reader)

        changed_count = sum(
            1 for row in rows if row.get(age_col, "") in unparseable_set
        )
        if changed_count:
            for row in rows:
                if row.get(age_col, "") in unparseable_set:
                    row[age_col] = "n/a"
            with open(tsv_path, "w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
                )
                writer.writeheader()
                writer.writerows(rows)
            warnings.append(
                f"Age column '{age_col}': normalized {changed_count} unparseable "
                f"value(s) to 'n/a' in {tsv_path.name} (auto-fix)."
            )

    return warnings


def auto_add_missing_value_sentinels(
    tsv_path: Path, annotations_path: Path
) -> List[str]:
    """
    For every annotated categorical column, collect unique values from the TSV
    and classify values not already in ``Levels`` or ``MissingValues``:

    * **NA-like** (``"-"``, ``"n/a"``, ``"NA"``, ``""`` etc.) → appended to
      ``MissingValues``.
    * **Whitespace-only variant of a known Level key** (e.g. ``"F "`` when
      ``"F"`` is a Level) → appended to ``MissingValues``.
    * **Non-NA unrecognized values** → left untouched so the
      ``"Missing categorical value annotations"`` Bagel error classifier can
      flag them.

    Returns a list of warning strings.  The annotations file is modified
    in-place when sentinels are added.
    """
    warnings: List[str] = []

    if not annotations_path.exists() or not tsv_path.exists():
        return warnings

    with open(annotations_path, "r", encoding="utf-8") as fh:
        annotations = json.load(fh)

    changed = False

    # Read TSV into a column-value map
    col_values: Dict[str, List[str]] = {}
    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            for col, val in row.items():
                col_values.setdefault(col, []).append(val)

    for col, col_data in annotations.items():
        ann = col_data.get("Annotations", {})
        if ann.get("VariableType") != "Categorical":
            continue

        levels: Dict[str, Any] = ann.get("Levels", {})
        missing_values: List[str] = ann.get("MissingValues", [])
        known_levels_stripped = {k.strip() for k in levels}

        tsv_values = set(col_values.get(col, []))

        for val in tsv_values:
            if val in levels or val in missing_values:
                continue
            val_stripped = val.strip()

            if val_stripped.lower() in {p.lower() for p in _NA_PATTERNS} or val_stripped == "":
                # NA-like → add as sentinel
                missing_values.append(val)
                changed = True
                warnings.append(
                    f"Column '{col}': auto-added NA-like value {val!r} to MissingValues."
                )
            elif val_stripped in known_levels_stripped and val_stripped != val:
                # Whitespace variant of a known Level → add as sentinel
                missing_values.append(val)
                changed = True
                warnings.append(
                    f"Column '{col}': auto-added whitespace variant {val!r} "
                    f"(matches Level '{val_stripped}') to MissingValues."
                )
            # else: non-NA unrecognized — leave for error classifier

        ann["MissingValues"] = missing_values
        annotations[col]["Annotations"] = ann

    if changed:
        with open(annotations_path, "w", encoding="utf-8") as fh:
            json.dump(annotations, fh, indent=2)

    return warnings


def fix_missing_levels(tsv_path: Path, annotations_path: Path) -> List[str]:
    """
    For each ``VariableType == "Categorical"`` column this function:

    1. **Repairs** any existing ``Annotations.Levels`` entries that are in an
       invalid format (i.e. have a ``Description`` key instead of the required
       ``TermURL`` + ``Label`` pair).  Entries are repaired by looking up the
       value in the categorical terms config (case-insensitive); unknown values
       receive the placeholder term ``nb:Unresolved``.  Levels keys are written
       under the **preferred** (canonical) term, not the raw TSV value.

    2. **Adds** entries for observed TSV values that are absent from both
       ``Annotations.Levels`` and ``MissingValues``.  Again, known values are
       mapped to their controlled vocabulary term; unknown values receive
       ``nb:Unresolved``.  Levels keys use the preferred term.

    3. **Syncs** the top-level BIDS ``Levels`` block (``dict[str, str]``) so
       that bagel does not warn about missing BIDS Levels for categorical
       columns.

    4. **Rewrites the TSV** in-place, replacing alias values with their
       preferred term (e.g. ``"control"`` → ``"hc"``).

    This prevents bagel from raising "Invalid data dictionary schema" (when
    entries lack ``TermURL``/``Label``) or "missing categorical value
    annotations" errors.

    Returns a list of warning strings.  The annotations file is modified
    in-place when any change is made.
    """
    warnings_list: List[str] = []

    if not annotations_path.exists() or not tsv_path.exists():
        return warnings_list

    with open(annotations_path, "r", encoding="utf-8") as fh:
        annotations = json.load(fh)

    # Read TSV values per column
    col_values: Dict[str, List[str]] = {}
    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            for col, val in row.items():
                col_values.setdefault(col, []).append(val)

    changed = False
    # Per-column TSV rename maps: {column -> {raw_val -> preferred}}
    tsv_renames: Dict[str, Dict[str, str]] = {}

    for col, col_data in annotations.items():
        ann = col_data.get("Annotations", {})
        if ann.get("VariableType") != "Categorical":
            continue

        nb_levels: Dict[str, Any] = ann.get("Levels", {})
        bids_levels: Dict[str, Any] = col_data.get("Levels") or {}
        missing_values: List[str] = ann.get("MissingValues", [])
        na_lower = {p.lower() for p in _NA_PATTERNS}

        # ── Step 1: repair existing entries that lack TermURL / Label ─────
        for val, entry in list(nb_levels.items()):
            if isinstance(entry, dict) and "TermURL" not in entry:
                preferred = _ALIAS_TO_PREFERRED.get(val.strip().lower())
                term = _PREFERRED_TO_TERM.get(preferred) if preferred else None
                canonical_key = preferred if preferred else val
                nb_levels.pop(val)
                nb_levels[canonical_key] = term if term else {
                    "TermURL": "nb:Unresolved",
                    "Label": val,
                }
                bids_levels.setdefault(
                    canonical_key, nb_levels[canonical_key]["Label"])
                if preferred and preferred != val:
                    tsv_renames.setdefault(col, {})[val] = preferred
                changed = True
                warnings_list.append(
                    f"fix_missing_levels: column '{col}': repaired invalid "
                    f"Levels entry for {val!r}."
                )

        # ── Step 2: add entries for values observed in TSV but not yet in Levels
        for val in set(col_values.get(col, [])):
            preferred = _ALIAS_TO_PREFERRED.get(val.strip().lower())
            canonical_key = preferred if preferred else val
            if canonical_key in nb_levels or val in missing_values:
                continue
            if val.strip().lower() in na_lower or val.strip() == "":
                continue
            term = _PREFERRED_TO_TERM.get(preferred) if preferred else None
            nb_levels[canonical_key] = term if term else {
                "TermURL": "nb:Unresolved",
                "Label": val,
            }
            bids_levels.setdefault(
                canonical_key, nb_levels[canonical_key]["Label"])
            if preferred and preferred != val:
                tsv_renames.setdefault(col, {})[val] = preferred
            changed = True
            warnings_list.append(
                f"fix_missing_levels: column '{col}': added Levels entry for "
                f"{val!r} (review required)."
            )

        ann["Levels"] = nb_levels
        annotations[col]["Annotations"] = ann
        # Sync BIDS top-level Levels so bagel does not warn about missing Levels
        if nb_levels:
            annotations[col]["Levels"] = bids_levels

    if changed:
        with open(annotations_path, "w", encoding="utf-8") as fh:
            json.dump(annotations, fh, indent=2)

    # ── Step 4: rewrite TSV alias values to preferred terms ───────────────
    if tsv_renames:
        with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
            lines = fh.readlines()

        header = lines[0].rstrip("\r\n").split("\t")
        new_lines = [lines[0]]
        for line in lines[1:]:
            cells = line.rstrip("\r\n").split("\t")
            for col, rename_map in tsv_renames.items():
                if col in header:
                    idx = header.index(col)
                    if idx < len(cells) and cells[idx] in rename_map:
                        old_val = cells[idx]
                        cells[idx] = rename_map[old_val]
                        warnings_list.append(
                            f"fix_missing_levels: column '{col}': renamed TSV "
                            f"value {old_val!r} → {rename_map[old_val]!r}."
                        )
            new_lines.append("\t".join(cells) + "\n")

        with open(tsv_path, "w", encoding="utf-8") as fh:
            fh.writelines(new_lines)

    return warnings_list


def fix_single_column_tsv(tsv_path: Path) -> List[str]:
    """
    Detect and fix a TSV that uses a non-tab delimiter (comma or semicolon).

    Reads the first two lines to identify the dominant non-tab delimiter, then
    rewrites the file in place using tabs.  Only rewrites when at least two
    fields are found after splitting on the detected delimiter.

    Returns a list of warning strings (one entry if a fix was applied, empty
    otherwise).
    """
    warnings: List[str] = []

    if not tsv_path.exists():
        return warnings

    with open(tsv_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    if not lines:
        return warnings

    header = lines[0].rstrip("\n")

    # Already tab-separated?
    if "\t" in header:
        return warnings

    # Detect dominant delimiter
    delimiter = None
    for candidate in (",", ";", "|"):
        if len(header.split(candidate)) > 1:
            delimiter = candidate
            break

    if delimiter is None:
        return warnings

    rewritten = [l.rstrip("\n").replace(delimiter, "\t") + "\n" for l in lines]
    with open(tsv_path, "w", encoding="utf-8") as fh:
        fh.writelines(rewritten)

    warnings.append(
        f"fix_single_column_tsv: rewrote {tsv_path.name} replacing "
        f"'{delimiter}' with tabs (auto-fix)."
    )
    return warnings


def dedup_participant_ids(tsv_path: Path) -> List[str]:
    """
    Remove duplicate ``participant_id`` rows from a TSV, keeping the first
    occurrence of each ID.

    Returns a list of warning strings naming each dropped ID.
    """
    warnings: List[str] = []

    if not tsv_path.exists():
        return warnings

    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    id_col = "participant_id"
    if id_col not in fieldnames:
        return warnings

    seen: set = set()
    kept: List[Dict] = []
    for row in rows:
        pid = row.get(id_col, "").strip()
        if pid in seen:
            warnings.append(
                f"dedup_participant_ids: dropped duplicate participant_id '{pid}'."
            )
        else:
            seen.add(pid)
            kept.append(row)

    if len(kept) == len(rows):
        return warnings

    with open(tsv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(kept)

    return warnings


def fill_empty_id_rows(tsv_path: Path) -> List[str]:
    """
    Remove rows where ``participant_id`` is empty or whitespace-only.

    Writes the TSV back in-place after dropping such rows.

    Returns a list of warning strings describing how many rows were removed.
    """
    warnings: List[str] = []

    if not tsv_path.exists():
        return warnings

    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    id_col = "participant_id"
    if id_col not in fieldnames:
        return warnings

    kept: List[Dict] = []
    dropped = 0
    for row in rows:
        pid = row.get(id_col, "").strip()
        if not pid:
            dropped += 1
        else:
            kept.append(row)

    if dropped == 0:
        return warnings

    with open(tsv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(kept)

    warnings.append(
        f"fill_empty_id_rows: removed {dropped} row(s) with empty participant_id "
        f"from {tsv_path.name} (auto-fix)."
    )
    return warnings
