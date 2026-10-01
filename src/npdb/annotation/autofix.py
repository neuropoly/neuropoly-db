"""
Pre-processing / auto-fix functions for participants.tsv and annotations.

Handles age format detection, NA sentinel injection, categorical level
repair, duplicate row removal, and delimiter correction.
"""

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# NA-like sentinel patterns recognised by NeuroBagel
# ---------------------------------------------------------------------------

_NA_PATTERNS: set[str] = {"-", "n/a", "na", "N/A", "NA", "", "unknown", "?"}

# ---------------------------------------------------------------------------
# Categorical terms config loader
# ---------------------------------------------------------------------------

_CATEGORICAL_TERMS_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "categorical_terms.json"
)
categorical_terms_cache: tuple[dict[str, str], dict[str, dict[str, str]]] | None = None


def load_categorical_terms(
    path: Path,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Load and validate ``config/categorical_terms.json``."""
    with open(path, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = json.load(fh)

    alias_to_preferred: dict[str, str] = {}
    preferred_to_term: dict[str, dict[str, str]] = {}

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
        alias_to_preferred[preferred] = preferred
        for alias in entry["aliases"]:
            alias_to_preferred[alias] = preferred

    return alias_to_preferred, preferred_to_term


def _get_categorical_terms() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Lazily load and cache categorical terms from config."""
    global categorical_terms_cache
    if categorical_terms_cache is None:
        categorical_terms_cache = load_categorical_terms(_CATEGORICAL_TERMS_PATH)
    return categorical_terms_cache


# Age format detection — ordered list of (neurobagel_term, pattern) tuples.
_AGE_FORMAT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("nb:FromBounded", re.compile(r"^\d+(\.\d+)?\+$")),
    ("nb:FromRange", re.compile(r"^\d+(\.\d+)?-\d+(\.\d+)?$")),
    ("nb:FromISO8601", re.compile(r"^P\d")),
    ("nb:FromEuro", re.compile(r"^\d+,\d+$")),
    ("nb:FromBounded", re.compile(r"^\+\d+(\.\d+)?$")),
    ("nb:FromBounded", re.compile(r"^\d+(\.\d+)?-$")),
    ("nb:FromBounded", re.compile(r"^-\d+(\.\d+)?$")),
]

_AGE_NONSTANDARD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("nb:FromBounded", re.compile(r"^\+\d+(\.\d+)?$")),
    ("nb:FromBounded", re.compile(r"^\d+(\.\d+)?-$")),
    ("nb:FromBounded", re.compile(r"^-\d+(\.\d+)?$")),
]


def _detect_age_format(values: list[str]) -> str:
    """Detect the dominant NeuroBagel age format from a list of values."""
    non_empty = [v for v in values if v.strip()]
    if not non_empty:
        return "nb:FromFloat"

    counts: Counter[str] = Counter(
        next(
            (term for term, pat in _AGE_FORMAT_PATTERNS if pat.match(v.strip())),
            "nb:FromFloat",
        )
        for v in non_empty
    )
    return counts.most_common(1)[0][0]


def _is_plain_float(value: str) -> bool:
    """Return True if *value* (stripped) parses as a plain float or int."""
    try:
        float(value.strip())
        return True
    except ValueError:
        return False


def fix_age_format(tsv_path: Path, annotations_path: Path) -> list[str]:
    """Detect the age encoding in *tsv_path* and update *annotations_path*."""
    warnings: list[str] = []

    if not annotations_path.exists():
        return warnings

    with open(annotations_path, "r", encoding="utf-8") as fh:
        annotations = json.load(fh)

    age_col: str | None = None
    for col, col_data in annotations.items():
        ann = col_data.get("Annotations", {})
        if ann.get("IsAbout", {}).get("TermURL") == "nb:Age":
            age_col = col
            break

    if age_col is None or not tsv_path.exists():
        return warnings

    age_values: list[str] = []
    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if age_col not in (reader.fieldnames or []):
            return warnings
        for row in reader:
            age_values.append(row.get(age_col, ""))

    if not age_values:
        return warnings

    parseable: list[str] = []
    unparseable: list[str] = []
    for v in age_values:
        if any(
            pat.match(v.strip()) for _, pat in _AGE_FORMAT_PATTERNS
        ) or _is_plain_float(v):
            parseable.append(v)
        else:
            unparseable.append(v)

    if age_values and len(unparseable) / len(age_values) >= 0.9:
        warnings.append(
            f"Age column '{age_col}': ≥90 % of values are missing/NA "
            f"({len(unparseable)}/{len(age_values)}); age data may be incomplete."
        )

    ann_block = annotations[age_col].get("Annotations", {})
    annotations_changed = False

    if parseable:
        detected = _detect_age_format(parseable)
        current_fmt = ann_block.get("Format", {}).get("TermURL", "nb:FromFloat")
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

        nonstandard = [
            v
            for v in parseable
            if any(pat.match(v.strip()) for _, pat in _AGE_NONSTANDARD_PATTERNS)
        ]
        if nonstandard:
            examples = ", ".join(repr(e) for e in nonstandard[:3])
            suffix = " …" if len(nonstandard) > 3 else ""
            warnings.append(
                f"Age column '{age_col}': non-standard age notation detected "
                f"({examples}{suffix}). Accepted and mapped to '{detected}'. "
                "Consider normalising to canonical form (e.g. '89+' instead of "
                "'+89', '42+' instead of '42-' or '-42')."
            )
    else:
        warnings.append(
            f"Age column '{age_col}': no parseable age values found; "
            f"Format kept unchanged (all values will be treated as missing)."
        )

    if unparseable:
        existing_mv: list[str] = list(ann_block.get("MissingValues", []))
        added_to_mv: list[str] = []
        for v in dict.fromkeys(unparseable):
            if v not in existing_mv:
                existing_mv.append(v)
                added_to_mv.append(v)
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
) -> list[str]:
    """Add NA-like sentinel values to categorical Annotation MissingValues."""
    warnings: list[str] = []

    if not annotations_path.exists() or not tsv_path.exists():
        return warnings

    with open(annotations_path, "r", encoding="utf-8") as fh:
        annotations = json.load(fh)

    changed = False
    col_values: dict[str, list[str]] = {}
    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            for col, val in row.items():
                col_values.setdefault(col, []).append(val)

    for col, col_data in annotations.items():
        ann = col_data.get("Annotations", {})
        if ann.get("VariableType") != "Categorical":
            continue

        levels: dict[str, Any] = ann.get("Levels", {})
        missing_values: list[str] = ann.get("MissingValues", [])
        known_levels_stripped = {k.strip() for k in levels}
        tsv_values = set(col_values.get(col, []))

        for val in tsv_values:
            if val in levels or val in missing_values:
                continue
            val_stripped = val.strip()

            if (
                val_stripped.lower() in {p.lower() for p in _NA_PATTERNS}
                or val_stripped == ""
            ):
                missing_values.append(val)
                changed = True
                warnings.append(
                    f"Column '{col}': auto-added NA-like value {val!r} to MissingValues."
                )
            elif val_stripped in known_levels_stripped and val_stripped != val:
                missing_values.append(val)
                changed = True
                warnings.append(
                    f"Column '{col}': auto-added whitespace variant {val!r} "
                    f"(matches Level '{val_stripped}') to MissingValues."
                )

        ann["MissingValues"] = missing_values
        annotations[col]["Annotations"] = ann

    if changed:
        with open(annotations_path, "w", encoding="utf-8") as fh:
            json.dump(annotations, fh, indent=2)

    return warnings


def fix_missing_levels(tsv_path: Path, annotations_path: Path) -> list[str]:
    """Repair categorical levels and rewrite TSV alias values to preferred strings."""
    _alias_to_preferred, _preferred_to_term = _get_categorical_terms()
    warnings_list: list[str] = []

    if not annotations_path.exists() or not tsv_path.exists():
        return warnings_list

    with open(annotations_path, "r", encoding="utf-8") as fh:
        annotations = json.load(fh)

    col_values: dict[str, list[str]] = {}
    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            for col, val in row.items():
                col_values.setdefault(col, []).append(val)

    changed = False
    tsv_renames: dict[str, dict[str, str]] = {}

    for col, col_data in annotations.items():
        ann = col_data.get("Annotations", {})
        if ann.get("VariableType") != "Categorical":
            continue

        nb_levels: dict[str, Any] = ann.get("Levels", {})
        bids_levels: dict[str, Any] = col_data.get("Levels") or {}
        missing_values: list[str] = ann.get("MissingValues", [])
        na_lower = {p.lower() for p in _NA_PATTERNS}

        for val, entry in list(nb_levels.items()):
            if isinstance(entry, dict) and "TermURL" not in entry:
                preferred = _alias_to_preferred.get(val.strip().lower())
                term = _preferred_to_term.get(preferred) if preferred else None
                canonical_key = preferred if preferred else val
                nb_levels.pop(val)
                nb_levels[canonical_key] = (
                    term if term else {"TermURL": "nb:Unresolved", "Label": val}
                )
                bids_levels.setdefault(canonical_key, nb_levels[canonical_key]["Label"])
                if preferred and preferred != val:
                    tsv_renames.setdefault(col, {})[val] = preferred
                changed = True
                warnings_list.append(
                    f"fix_missing_levels: column '{col}': repaired invalid "
                    f"Levels entry for {val!r}."
                )

        for val in set(col_values.get(col, [])):
            preferred = _alias_to_preferred.get(val.strip().lower())
            canonical_key = preferred if preferred else val
            if canonical_key in nb_levels or val in missing_values:
                continue
            if val.strip().lower() in na_lower or val.strip() == "":
                continue
            term = _preferred_to_term.get(preferred) if preferred else None
            nb_levels[canonical_key] = (
                term if term else {"TermURL": "nb:Unresolved", "Label": val}
            )
            bids_levels.setdefault(canonical_key, nb_levels[canonical_key]["Label"])
            if preferred and preferred != val:
                tsv_renames.setdefault(col, {})[val] = preferred
            changed = True
            warnings_list.append(
                f"fix_missing_levels: column '{col}': added Levels entry for "
                f"{val!r} (review required)."
            )

        ann["Levels"] = nb_levels
        annotations[col]["Annotations"] = ann
        if nb_levels:
            annotations[col]["Levels"] = bids_levels

    if changed:
        with open(annotations_path, "w", encoding="utf-8") as fh:
            json.dump(annotations, fh, indent=2)

    if tsv_renames:
        with open(tsv_path, "r", encoding="utf-8") as fh:
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


def fix_single_column_tsv(tsv_path: Path) -> list[str]:
    """Detect and fix a TSV that uses a non-tab delimiter."""
    warnings: list[str] = []
    if not tsv_path.exists():
        return warnings

    with open(tsv_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    if not lines:
        return warnings

    header = lines[0].rstrip("\n")
    if "\t" in header:
        return warnings

    delimiter = None
    for candidate in (",", ";", "|"):
        if len(header.split(candidate)) > 1:
            delimiter = candidate
            break

    if delimiter is None:
        return warnings

    rewritten = [line.rstrip("\n").replace(delimiter, "\t") + "\n" for line in lines]
    with open(tsv_path, "w", encoding="utf-8") as fh:
        fh.writelines(rewritten)

    warnings.append(
        f"fix_single_column_tsv: rewrote {tsv_path.name} replacing "
        f"'{delimiter}' with tabs (auto-fix)."
    )
    return warnings


def dedup_participant_ids(tsv_path: Path) -> list[str]:
    """Remove duplicate participant_id rows from a TSV."""
    warnings: list[str] = []
    if not tsv_path.exists():
        return warnings

    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    id_col = "participant_id"
    if id_col not in fieldnames:
        return warnings

    seen: set[str] = set()
    kept: list[dict[str, str]] = []
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


def fill_empty_id_rows(tsv_path: Path) -> list[str]:
    """Remove rows where participant_id is empty or whitespace-only."""
    warnings: list[str] = []
    if not tsv_path.exists():
        return warnings

    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    id_col = "participant_id"
    if id_col not in fieldnames:
        return warnings

    kept: list[dict[str, str]] = []
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


__all__ = [
    "_NA_PATTERNS",
    "_AGE_FORMAT_PATTERNS",
    "_AGE_NONSTANDARD_PATTERNS",
    "_get_categorical_terms",
    "_detect_age_format",
    "_is_plain_float",
    "load_categorical_terms",
    "fix_age_format",
    "auto_add_missing_value_sentinels",
    "fix_missing_levels",
    "fix_single_column_tsv",
    "dedup_participant_ids",
    "fill_empty_id_rows",
]
