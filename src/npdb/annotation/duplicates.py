"""
Post-processing module for handling duplicate field mappings in phenotype annotations.

After annotation tool exports phenotypes.tsv and phenotypes_annotations.json,
multiple columns may map to the same Neurobagel variable. This module resolves
duplicates per Neurobagel specifications:

1. For Identifier variables (nb:ParticipantID, nb:SessionID):
   - Keeps PRIMARY (highest-confidence) - annotated in JSON
   - Keeps ONE ALTERNATE (second-ranked) - renamed to alt_participant_id/alt_session_id in TSV
   - DROPS third and beyond entirely
   - Alternate columns are unannotated data columns

2. For Other variables:
   - Keeps only PRIMARY (highest-confidence) in both TSV and JSON
   - DROPS all lower-confidence duplicates entirely

This preserves ranking/provenance info and avoids re-running algorithms.
Reference: https://neurobagel.org/user_guide/data_prep/#multiple-participant-or-session-id-columns
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


# Identifier variables that can have alternates per Neurobagel spec
IDENTIFIER_VARIABLES = {
    "nb:ParticipantID",
    "nb:SessionID",
}


@dataclass
class ColumnMapping:
    """Mapping info for a column."""
    column_name: str
    variable: str
    confidence: float
    source: str
    rationale: str


def load_annotations(json_path: Path) -> Dict[str, Any]:
    """Load phenotypes_annotations.json."""
    with open(json_path, 'r') as f:
        return json.load(f)


def save_annotations(json_path: Path, data: Dict[str, Any]) -> None:
    """Save phenotypes_annotations.json."""
    with open(json_path, 'w') as f:
        json.dump(data, f, indent=2)


def load_tsv_lines(tsv_path: Path) -> List[str]:
    """Load all TSV lines including header."""
    with open(tsv_path, 'r', encoding='utf-8') as f:
        return f.readlines()


def save_tsv(tsv_path: Path, lines: List[str]) -> None:
    """Save TSV lines."""
    with open(tsv_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)


def group_by_variable(annotations: Dict[str, Any]) -> Dict[str, List[ColumnMapping]]:
    """
    Group columns by their mapped variable.

    Returns:
        Dict mapping variable -> List[ColumnMapping] sorted by confidence (descending)
    """
    groups: Dict[str, List[ColumnMapping]] = {}

    for col_name, mapping_info in annotations.items():
        variable = mapping_info.get("variable", "unknown")
        confidence = mapping_info.get("confidence", 0.0)
        source = mapping_info.get("source", "unknown")
        rationale = mapping_info.get("rationale", "")

        if variable not in groups:
            groups[variable] = []

        groups[variable].append(
            ColumnMapping(
                column_name=col_name,
                variable=variable,
                confidence=confidence,
                source=source,
                rationale=rationale
            )
        )

    # Sort each group by confidence descending
    for variable in groups:
        groups[variable].sort(key=lambda m: m.confidence, reverse=True)

    return groups


def resolve_duplicates(
    annotations: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, str], List[str]]:
    """
    Resolve duplicate field mappings per Neurobagel specifications.

    For Identifier variables:
    - Keep primary (highest-confidence) - annotated
    - Keep ONE alternate (second-ranked) - renamed and unannotated
    - Drop third+ entirely

    For other variables: keep only primary in both TSV and JSON.

    Args:
        annotations: phenotypes_annotations.json data

    Returns:
        Tuple of:
        - Updated annotations dict (with duplicates removed)
        - Rename mapping for TSV (old_name -> new_name for alternates)
        - List of columns to drop from TSV (duplicates beyond second rank)
    """
    groups = group_by_variable(annotations)
    renames: Dict[str, str] = {}  # old_name -> new_name (for alternates)
    drops: List[str] = []  # Columns to remove from TSV entirely

    # Track which annotations to remove
    to_remove = []

    for variable, mappings in groups.items():
        if len(mappings) == 1:
            # No duplicates for this variable
            continue

        if variable in IDENTIFIER_VARIABLES:
            # Identifier: keep primary + one alternate (renamed, unannotated)
            primary = mappings[0]  # Highest confidence

            # Determine alternate name based on variable type
            if variable == "nb:ParticipantID":
                alt_name = "alt_participant_id"
            elif variable == "nb:SessionID":
                alt_name = "alt_session_id"
            else:
                alt_name = f"alt_{variable.split(':')[-1].lower()}"

            for i, alternate in enumerate(mappings[1:], start=1):
                if i == 1:
                    # Second-ranked: keep as alternate (renamed in TSV, unannotated)
                    renames[alternate.column_name] = alt_name
                    to_remove.append(alternate.column_name)  # Remove from JSON
                    print(
                        f"ℹ Identifier alternate: '{alternate.column_name}' "
                        f"(confidence {alternate.confidence:.2f}) "
                        f"→ renamed to '{alt_name}' (unannotated) "
                        f"(primary: '{primary.column_name}' confidence {primary.confidence:.2f})"
                    )
                else:
                    # Third+: drop entirely from both TSV and JSON
                    to_remove.append(alternate.column_name)  # Remove from JSON
                    drops.append(alternate.column_name)  # Remove from TSV
                    print(
                        f"✓ Identifier dropped (3rd+): '{alternate.column_name}' "
                        f"(confidence {alternate.confidence:.2f}) "
                        f"→ Neurobagel only supports primary + one alternate"
                    )

        else:
            # Non-identifier: keep primary, drop ALL others
            primary = mappings[0]  # Highest confidence

            for duplicate in mappings[1:]:
                # Remove from TSV and JSON
                to_remove.append(duplicate.column_name)
                drops.append(duplicate.column_name)
                print(
                    f"✓ Duplicate dropped: '{duplicate.column_name}' "
                    f"(confidence {duplicate.confidence:.2f}) "
                    f"→ keeping '{primary.column_name}' "
                    f"(confidence {primary.confidence:.2f}) for {variable}"
                )

    # Remove annotations
    for col_name in to_remove:
        annotations.pop(col_name, None)

    return annotations, renames, drops


def update_tsv(
    tsv_path: Path,
    renames: Dict[str, str],
    drops: List[str]
) -> None:
    """
    Update TSV file: rename alternate ID columns and remove duplicate columns.

    Args:
        tsv_path: Path to phenotypes.tsv
        renames: Mapping old_name -> new_name (for ID alternates)
        drops: List of column names to remove entirely (duplicates beyond second rank)
    """
    if not renames and not drops:
        return  # No changes needed

    lines = load_tsv_lines(tsv_path)
    if not lines:
        return

    # Parse header
    header_line = lines[0].rstrip('\n')
    headers = header_line.split('\t')

    # Build new header with renames and drops
    new_headers = []
    keep_indices = []

    for i, header in enumerate(headers):
        if header in drops:
            # Skip dropped columns
            continue

        if header in renames:
            # Rename this column
            new_headers.append(renames[header])
        else:
            # Keep as-is
            new_headers.append(header)

        keep_indices.append(i)

    # Update header line
    lines[0] = '\t'.join(new_headers) + '\n'

    # Update data lines: keep only keep_indices
    updated_lines = [lines[0]]  # Keep new header

    for line in lines[1:]:
        parts = line.rstrip('\n').split('\t')
        # Select only columns to keep
        kept_parts = [parts[i] if i < len(parts) else '' for i in keep_indices]
        updated_lines.append('\t'.join(kept_parts) + '\n')

    save_tsv(tsv_path, updated_lines)

    if renames:
        print(f"✓ TSV updated: {len(renames)} alternate column(s) renamed")
    if drops:
        print(f"✓ TSV updated: {len(drops)} duplicate column(s) removed")


def resolve_phenotype_duplicates(
    phenotypes_tsv_path: Path,
    phenotypes_annotations_path: Path,
    verbose: bool = True
) -> None:
    """
    Resolve duplicate field mappings in annotation outputs.

    Updates both phenotypes.tsv and phenotypes_annotations.json in-place.

    Per Neurobagel data prep guidelines:
    - Identifier columns: keep primary + one alternate (renamed)
    - Other duplicates: keep only the highest-confidence

    Args:
        phenotypes_tsv_path: Path to phenotypes.tsv
        phenotypes_annotations_path: Path to phenotypes_annotations.json
        verbose: Print operations
    """
    if verbose:
        print(f"\n→ Post-processing duplicate fields...")

    # Load annotations
    annotations = load_annotations(phenotypes_annotations_path)

    # Resolve duplicates
    updated_annotations, renames, drops = resolve_duplicates(annotations)

    # Update TSV (rename alternates, drop duplicates)
    update_tsv(phenotypes_tsv_path, renames, drops)

    # Update JSON annotations
    save_annotations(phenotypes_annotations_path, updated_annotations)

    if verbose:
        if renames or drops:
            print(f"✓ Duplicate resolution complete")
        else:
            print(f"ℹ No duplicates found; annotations already clean")
