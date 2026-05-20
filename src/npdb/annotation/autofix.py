"""
Pre-processing / auto-fix functions for participants.tsv and annotations.

Handles age format detection, NA sentinel injection, categorical level
repair, duplicate row removal, and delimiter correction.  Extracted from
standardize.py so that the two concerns (BIDS structural standardization vs.
data quality auto-fixes) live in separate modules.
"""

import csv
import json
import re
from collections import Counter
from pathlib import Path

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
_categorical_terms_cache: tuple[dict[str, str], dict[str, dict[str, str]]] | None = None


def load_categorical_terms(
    path: Path,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Load and validate ``config/categorical_terms.json``.

    Returns a tuple of two flat dicts:

    * ``alias_to_preferred`` — every alias **and** the preferred key itself
      maps to the preferred key (e.g. ``{"hc": "hc", "control": "hc", ...}``).
    * ``preferred_to_term`` — preferred key → ``{"TermURL": ..., "Label": ...}``.

    Raises :exc:`ValueError` on malformed entries (missing ``TermURL`` or
    ``Label`` keys, or non-list ``aliases``).
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw: dict[str, any] = json.load(fh)

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
        # The preferred key itself is also a valid lookup key
        alias_to_preferred[preferred] = preferred
        for alias in entry["aliases"]:
            alias_to_preferred[alias] = preferred

    return alias_to_preferred, preferred_to_term


def _get_categorical_terms() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Lazily load and cache categorical terms from config.

    Deferred to first call so that importing this module never fails due to
    a missing config file.
    """
    global _categorical_terms_cache
    if _categorical_terms_cache is None:
        _categorical_terms_cache = load_categorical_terms(_CATEGORICAL_TERMS_PATH)
    return _categorical_terms_cache


# Age format detection — ordered list of (neurobagel_term, pattern) tuples.
# Multiple patterns may map to the same term; the first match wins per value.
# Standard notations come first so they take priority in majority voting.
_AGE_FORMAT_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Standard / canonical notations
    ("nb:FromBounded", re.compile(r"^\d+(\.\d+)?\+$")),  # 89+
    ("nb:FromRange", re.compile(r"^\d+(\.\d+)?-\d+(\.\d+)?$")),  # 18-25
    ("nb:FromISO8601", re.compile(r"^P\d")),  # P30Y
    ("nb:FromEuro", re.compile(r"^\d+,\d+$")),  # 42,5
    # Non-standard but accepted single-ended notations — all map to FromBounded
    ("nb:FromBounded", re.compile(r"^\+\d+(\.\d+)?$")),  # +89
    ("nb:FromBounded", re.compile(r"^\d+(\.\d+)?-$")),  # 42-
    ("nb:FromBounded", re.compile(r"^-\d+(\.\d+)?$")),  # -42
]

# Subset used only to emit soft warnings (non-standard but accepted forms)
_AGE_NONSTANDARD_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("nb:FromBounded", re.compile(r"^\+\d+(\.\d+)?$")),  # +89
    ("nb:FromBounded", re.compile(r"^\d+(\.\d+)?-$")),  # 42-
    ("nb:FromBounded", re.compile(r"^-\d+(\.\d+)?$")),  # -42
]


def _detect_age_format(values: list[str]) -> str:
    """
    Detect the NeuroBagel age format from a list of non-missing age values.

    Checks each pattern in ``_AGE_FORMAT_PATTERNS`` in order and picks the
    term that matches the most values.  Falls back to ``nb:FromFloat``.
    """
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
    warnings: list[str] = []

    if not annotations_path.exists():
        return warnings

    with open(annotations_path, "r", encoding="utf-8") as fh:
        annotations = json.load(fh)

    # Find the age column — the one whose IsAbout.TermURL == "nb:Age"
    age_col: str | None = None
    for col, col_data in annotations.items():
        ann = col_data.get("Annotations", {})
        if ann.get("IsAbout", {}).get("TermURL") == "nb:Age":
            age_col = col
            break

    if age_col is None:
        return warnings

    if not tsv_path.exists():
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

    # Classify: parseable (format regex match or plain float) vs unparseable
    parseable: list[str] = []
    unparseable: list[str] = []
    for v in age_values:
        if any(
            pat.match(v.strip()) for _, pat in _AGE_FORMAT_PATTERNS
        ) or _is_plain_float(v):
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

        # Warn (non-breaking) about non-standard single-ended notations
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
        existing_mv: list[str] = list(ann_block.get("MissingValues", []))
        added_to_mv: list[str] = []
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
) -> list[str]:
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
    warnings: list[str] = []

    if not annotations_path.exists() or not tsv_path.exists():
        return warnings

    with open(annotations_path, "r", encoding="utf-8") as fh:
        annotations = json.load(fh)

    changed = False

    # Read TSV into a column-value map
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

        levels: dict[str, any] = ann.get("Levels", {})
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


def fix_missing_levels(tsv_path: Path, annotations_path: Path) -> list[str]:
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

    Returns a list of warning strings.  The annotations file is modified
    in-place when any change is made.
    """
    _alias_to_preferred, _preferred_to_term = _get_categorical_terms()
    warnings_list: list[str] = []

    if not annotations_path.exists() or not tsv_path.exists():
        return warnings_list

    with open(annotations_path, "r", encoding="utf-8") as fh:
        annotations = json.load(fh)

    # Read TSV values per column
    col_values: dict[str, list[str]] = {}
    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            for col, val in row.items():
                col_values.setdefault(col, []).append(val)

    changed = False
    # Per-column TSV rename maps: {column -> {raw_val -> preferred}}
    tsv_renames: dict[str, dict[str, str]] = {}

    for col, col_data in annotations.items():
        ann = col_data.get("Annotations", {})
        if ann.get("VariableType") != "Categorical":
            continue

        nb_levels: dict[str, any] = ann.get("Levels", {})
        bids_levels: dict[str, any] = col_data.get("Levels") or {}
        missing_values: list[str] = ann.get("MissingValues", [])
        na_lower = {p.lower() for p in _NA_PATTERNS}

        # ── Step 1: repair existing entries that lack TermURL / Label ─────
        for val, entry in list(nb_levels.items()):
            if isinstance(entry, dict) and "TermURL" not in entry:
                preferred = _alias_to_preferred.get(val.strip().lower())
                term = _preferred_to_term.get(preferred) if preferred else None
                canonical_key = preferred if preferred else val
                nb_levels.pop(val)
                nb_levels[canonical_key] = (
                    term
                    if term
                    else {
                        "TermURL": "nb:Unresolved",
                        "Label": val,
                    }
                )
                bids_levels.setdefault(canonical_key, nb_levels[canonical_key]["Label"])
                if preferred and preferred != val:
                    tsv_renames.setdefault(col, {})[val] = preferred
                changed = True
                warnings_list.append(
                    f"fix_missing_levels: column '{col}': repaired invalid "
                    f"Levels entry for {val!r}."
                )

        # ── Step 2: add entries for values observed in TSV but not yet in Levels
        for val in set(col_values.get(col, [])):
            preferred = _alias_to_preferred.get(val.strip().lower())
            canonical_key = preferred if preferred else val
            if canonical_key in nb_levels or val in missing_values:
                continue
            if val.strip().lower() in na_lower or val.strip() == "":
                continue
            term = _preferred_to_term.get(preferred) if preferred else None
            nb_levels[canonical_key] = (
                term
                if term
                else {
                    "TermURL": "nb:Unresolved",
                    "Label": val,
                }
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
        # Sync BIDS top-level Levels so bagel does not warn about missing Levels
        if nb_levels:
            annotations[col]["Levels"] = bids_levels

    if changed:
        with open(annotations_path, "w", encoding="utf-8") as fh:
            json.dump(annotations, fh, indent=2)

    # ── Step 4: rewrite TSV alias values to preferred terms ───────────────
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
    """
    Detect and fix a TSV that uses a non-tab delimiter (comma or semicolon).

    Reads the first two lines to identify the dominant non-tab delimiter, then
    rewrites the file in place using tabs.  Only rewrites when at least two
    fields are found after splitting on the detected delimiter.

    Returns a list of warning strings (one entry if a fix was applied, empty
    otherwise).
    """
    warnings: list[str] = []

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


def dedup_participant_ids(tsv_path: Path) -> list[str]:
    """
    Remove duplicate ``participant_id`` rows from a TSV, keeping the first
    occurrence of each ID.

    Returns a list of warning strings naming each dropped ID.
    """
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

    seen: set = set()
    kept: list[dict] = []
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
    """
    Remove rows where ``participant_id`` is empty or whitespace-only.

    Writes the TSV back in-place after dropping such rows.

    Returns a list of warning strings describing how many rows were removed.
    """
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

    kept: list[dict] = []
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
