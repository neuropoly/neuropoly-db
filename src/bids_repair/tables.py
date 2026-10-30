"""Portable table cleanup, header translation and column editing."""

import csv
import json
import tempfile
from pathlib import Path
from typing import Any


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return [row for row in csv.reader(stream, delimiter="\t") if row]


def _write_rows(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream, delimiter="\t", lineterminator="\n").writerows(rows)


def strip_tsv_cell_whitespace(tsv_path: Path) -> list[str]:
    if not tsv_path.exists():
        return []

    rows = _read_rows(tsv_path)
    changed = []
    for index, row in enumerate(rows, 1):
        for position, value in enumerate(row):
            stripped = value.strip()
            if stripped != value:
                row[position] = stripped
                changed.append(f"row {index}: {value!r} → {stripped!r}")

    if not changed:
        return []

    _write_rows(tsv_path, rows)
    shown = "; ".join(changed[:10])
    more = f"; … {len(changed) - 10} more" if len(changed) > 10 else ""
    return [
        f"strip_tsv_cell_whitespace: trimmed whitespace in {len(changed)} "
        f"cell(s) of {tsv_path.name}: {shown}{more}."
    ]


def fix_trailing_empty_tsv_cells(tsv_path: Path) -> list[str]:
    if not tsv_path.exists():
        return []

    rows = _read_rows(tsv_path)
    if not rows:
        return []

    width = len(rows[0])
    corrected = 0
    for index, row in enumerate(rows[1:], 2):
        if len(row) <= width:
            continue
        if any(value != "" for value in row[width:]):
            raise ValueError(
                f"{tsv_path.name}: row {index} has non-empty cells beyond "
                f"the {width} header columns; refusing to discard data."
            )
        del row[width:]
        corrected += 1

    if corrected:
        _write_rows(tsv_path, rows)
        return [
            f"fix_trailing_empty_tsv_cells: removed surplus empty cells from "
            f"{corrected} row(s) in {tsv_path.name}."
        ]
    return []


def fix_single_column_tsv(tsv_path: Path) -> list[str]:
    if not tsv_path.exists():
        return []

    with tsv_path.open(newline="", encoding="utf-8") as stream:
        header = stream.readline()
        if not header or "\t" in header:
            return []
        delimiter = next((d for d in (",", ";", "|") if d in header), None)
        if delimiter is None:
            return []
        stream.seek(0)
        rows = list(csv.reader(stream, delimiter=delimiter))

    _write_rows(tsv_path, rows)
    return [
        f"fix_single_column_tsv: rewrote {tsv_path.name} replacing "
        f"'{delimiter}' with tabs (auto-fix)."
    ]


def dedup_participant_ids(tsv_path: Path) -> list[str]:
    if not tsv_path.exists():
        return []

    rows = _read_rows(tsv_path)
    if not rows or "participant_id" not in rows[0]:
        return []

    position = rows[0].index("participant_id")
    seen: set[str] = set()
    kept = [rows[0]]
    warnings = []
    for row in rows[1:]:
        if len(row) <= position:
            raise ValueError(f"Malformed participant row in {tsv_path}")
        participant = row[position].strip()
        if participant in seen:
            warnings.append(
                f"dedup_participant_ids: dropped duplicate participant_id '{participant}'."
            )
        else:
            seen.add(participant)
            kept.append(row)

    if warnings:
        _write_rows(tsv_path, kept)
    return warnings


def fill_empty_id_rows(tsv_path: Path) -> list[str]:
    if not tsv_path.exists():
        return []

    rows = _read_rows(tsv_path)
    if not rows or "participant_id" not in rows[0]:
        return []

    position = rows[0].index("participant_id")
    kept = [rows[0]]
    for row in rows[1:]:
        if len(row) <= position:
            raise ValueError(f"Malformed participant row in {tsv_path}")
        if row[position].strip():
            kept.append(row)

    dropped = len(rows) - len(kept)
    if not dropped:
        return []

    _write_rows(tsv_path, kept)
    return [
        f"fill_empty_id_rows: removed {dropped} row(s) with empty participant_id "
        f"from {tsv_path.name} (auto-fix)."
    ]


def normalize_participants_tsv(tsv_path: Path, *, dry_run: bool = False) -> list[str]:
    if dry_run:
        with tempfile.TemporaryDirectory(prefix="bids-repair-preview-") as directory:
            preview = Path(directory) / tsv_path.name
            preview.write_bytes(tsv_path.read_bytes())
            return normalize_participants_tsv(preview)

    warnings = []
    for cleanup in (
        fix_single_column_tsv,
        fix_trailing_empty_tsv_cells,
        strip_tsv_cell_whitespace,
        fill_empty_id_rows,
        dedup_participant_ids,
    ):
        warnings.extend(cleanup(tsv_path))

    return warnings


def load_header_map(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Header map must be a JSON object (dict)")

    for key, value in data.items():
        if not isinstance(value, dict):
            raise ValueError(
                f"Header map values must be objects, invalid value for key '{key}'"
            )
        aliases = value.get("aliases")
        if not isinstance(aliases, list) or not all(
            isinstance(v, str) for v in aliases
        ):
            raise ValueError(f"'aliases' must be a list of strings for key '{key}'")
        if value.get("variable") is not None and not isinstance(value["variable"], str):
            raise ValueError(f"'variable' must be a string for key '{key}'")

    return data


def rename_columns(
    tsv_path: Path, renames: dict[str, str], *, dry_run: bool = False
) -> dict[str, str]:
    rows = _read_rows(tsv_path)
    if not rows:
        return {}

    headers = [renames.get(header, header) for header in rows[0]]
    if len(headers) != len(set(headers)):
        raise ValueError("Conflicting header map: multiple columns map to the same key")

    if not dry_run:
        rows[0] = headers
        _write_rows(tsv_path, rows)
    else:
        print("Dry-run: header renames that would be applied:")
        for old, new in renames.items():
            print(f"  {old} → {new}")

    return renames


def apply_header_map(
    tsv_path: Path, header_map: dict[str, dict[str, Any]], dry_run: bool = False
) -> dict[str, str]:
    variants: dict[str, str] = {}
    for desired, entry in header_map.items():
        for alias in entry["aliases"]:
            lowered = alias.lower()
            if lowered in variants and variants[lowered] != desired:
                raise ValueError(
                    f"Ambiguous header map: variant '{alias}' appears under multiple keys"
                )
            variants[lowered] = desired

    rows = _read_rows(tsv_path)
    if not rows:
        return {}

    renames = {
        header: variants[header.lower()]
        for header in rows[0]
        if header.lower() in variants and header != variants[header.lower()]
    }
    if renames:
        rename_columns(tsv_path, renames, dry_run=dry_run)

    return renames


def add_columns(
    tsv_path: Path, columns: list[str], *, dry_run: bool = False
) -> list[str]:
    rows = _read_rows(tsv_path)
    if not rows:
        raise ValueError(f"TSV file is empty: {tsv_path}")

    missing = [column for column in dict.fromkeys(columns) if column not in rows[0]]
    if not dry_run and missing:
        rows[0].extend(missing)
        for row in rows[1:]:
            row.extend(["n/a"] * len(missing))
        _write_rows(tsv_path, rows)
    return missing
