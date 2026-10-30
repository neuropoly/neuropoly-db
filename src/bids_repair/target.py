"""Enforce the native beta's exact bundled-schema compatibility, without downloads."""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from bidsschematools.schema import load_schema

from bids_repair.native import COVERAGE, bundled_schema_path, schema_document


def acquire_target_schema(version: str, schema_path: Path | None = None) -> Path | None:
    """Reject unsupported targets before any validation or repair takes place."""
    bundled = schema_document()
    if version != bundled["bids_version"]:
        raise ValueError(
            f"Unsupported target BIDS version {version}; the pinned Rust validator "
            f"supports BIDS {bundled['bids_version']} (schema "
            f"{bundled['schema_version']}) only. An alternate Python schema "
            "cannot retarget the native beta."
        )

    if schema_path is not None:
        if schema_path.is_dir():
            supplied = load_schema(str(schema_path)).to_dict()
        else:
            supplied = json.loads(schema_path.read_text(encoding="utf-8"))
        if json.dumps(supplied, sort_keys=True) != json.dumps(bundled, sort_keys=True):
            raise ValueError(
                "The local schema is incompatible with the pinned Rust validator; "
                "only a structurally identical copy of its bundled schema is supported."
            )
    return schema_path


@contextmanager
def target_schema(
    version: str, schema_path: str | Path | None = None
) -> Iterator[Path]:
    selected = acquire_target_schema(
        version, Path(schema_path) if schema_path is not None else None
    )
    yield selected or bundled_schema_path()


def add_target_findings(
    root: Path,
    report: dict[str, Any],
    version: str,
    schema_path: str | Path | None = None,
) -> None:
    acquire_target_schema(
        version, Path(schema_path) if schema_path is not None else None
    )
    report["target_bids_version"] = version
    report["coverage"] = COVERAGE
