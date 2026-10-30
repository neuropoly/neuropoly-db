"""Explicit, provider-independent participants and layout standardization."""

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from bids_repair.dataset import prepare_dataset
from bids_repair.paths import assert_safe_path
from bids_repair.repair import _validate_dataset_root
from bids_repair.sidecars import build_participants_sidecar, validate_bids_sidecar
from bids_repair.tables import add_columns, apply_header_map, normalize_participants_tsv
from bids_repair.target import target_schema
from bids_repair.utils import parse_tsv_columns


def standardize_dataset(
    dataset: str | Path,
    *,
    header_map: dict[str, dict[str, Any]] | None = None,
    column_metadata: dict[str, dict[str, Any]] | None = None,
    add_missing_columns: list[str] | None = None,
    prepare_layout: bool = False,
    dry_run: bool = False,
    target_version: str | None = None,
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    root = _validate_dataset_root(dataset)
    if target_version is not None:
        with target_schema(target_version, schema_path):
            pass
    elif schema_path is not None:
        raise ValueError("schema_path requires target_version")

    metadata = {} if column_metadata is None else column_metadata
    if not isinstance(metadata, dict) or not all(
        isinstance(key, str) and isinstance(entry, dict)
        for key, entry in metadata.items()
    ):
        raise ValueError("Column metadata must map column names to JSON objects")

    for path in root.rglob("*"):
        assert_safe_path(root, path, must_exist=True)

    if dry_run:
        with tempfile.TemporaryDirectory(prefix="bids-repair-preview-") as directory:
            preview = Path(directory) / root.name

            def copy_metadata(source: str, destination: str) -> str:
                # Layout discovery needs image names, not multi-gigabyte payloads.
                if Path(source).name in {"participants.tsv", "participants.json"}:
                    return shutil.copy2(source, destination)
                Path(destination).touch()
                return destination

            shutil.copytree(root, preview, copy_function=copy_metadata)
            result = standardize_dataset(
                preview,
                header_map=header_map,
                column_metadata=metadata,
                add_missing_columns=add_missing_columns,
                prepare_layout=prepare_layout,
            )
        result.update(
            dataset=str(root), dry_run=True, target_bids_version=target_version
        )
        return result

    if prepare_layout:
        prepare_dataset(root)

    participants = root / "participants.tsv"
    sidecar_path = root / "participants.json"
    assert_safe_path(root, sidecar_path, must_exist=False)
    existing = (
        json.loads(sidecar_path.read_text(encoding="utf-8"))
        if sidecar_path.exists()
        else {}
    )
    if not isinstance(existing, dict):
        raise ValueError("participants.json must contain an object")
    if not all(isinstance(entry, dict) for entry in existing.values()):
        raise ValueError("participants.json column entries must contain objects")

    warnings = normalize_participants_tsv(participants)
    renames = apply_header_map(participants, header_map) if header_map else {}
    added = add_columns(participants, add_missing_columns or [])
    existing = {
        renames.get(column, column): entry for column, entry in existing.items()
    }
    _, sidecar_warnings = validate_bids_sidecar({**existing, **metadata})
    warnings.extend(sidecar_warnings)

    sidecar = build_participants_sidecar(
        parse_tsv_columns(participants), metadata, existing
    )
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    return {
        "dataset": str(root),
        "dry_run": False,
        "target_bids_version": target_version,
        "warnings": warnings,
        "renamed_columns": renames,
        "added_columns": added,
        "participants_json": sidecar,
        "prepared_layout": prepare_layout,
    }
