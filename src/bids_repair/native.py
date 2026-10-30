"""Locate the pinned native validator without downloading or compiling at runtime."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SOURCE_COMMIT = "b5c11828e104be88034ecccbe220ad1a20c72dfe"
RUST_VERSION = "0.1.20260517"
REFERENCE_VERSION = "2.4.1"
REFERENCE_COMMIT = "94ea9719efbbac7531fed8ad8a2a7bf764cee51f"
PACKAGE_BIN = Path(__file__).parent / "_bin"
COVERAGE = (
    "rordenlab/bids-validator-rs beta: bundled-schema filename, metadata, TSV "
    "and NIfTI header checks; not certified complete official-validator parity."
)


class NativeValidatorError(RuntimeError):
    """The installed bundle or source-development validator cannot be used."""


def source_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").is_file() and (
        root / "tools/bids-validator-source.json"
    ).is_file():
        return root
    return None


def bundled_schema_path() -> Path:
    if PACKAGE_BIN.exists():
        path = PACKAGE_BIN / "schema.json"
    else:
        root = source_root()
        if root is None:
            raise NativeValidatorError(
                "The installed validator bundle is missing; reinstall npdb."
            )
        path = root / "tools/bids-validator-rust/schemas/schema-1.2.1.json"
    if not path.is_file():
        raise NativeValidatorError(f"The pinned validator schema is missing: {path}")
    return path


def schema_document() -> dict[str, Any]:
    try:
        document = json.loads(bundled_schema_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NativeValidatorError(
            f"Cannot read the pinned validator schema: {exc}"
        ) from exc
    if not isinstance(document, dict) or not all(
        isinstance(document.get(key), str) for key in ("bids_version", "schema_version")
    ):
        raise NativeValidatorError(
            "The pinned validator schema has invalid version metadata."
        )
    return document


def validator_executable() -> str:
    name = "bids-validator.exe" if sys.platform == "win32" else "bids-validator"
    if PACKAGE_BIN.exists():
        executable = PACKAGE_BIN / name
        try:
            provenance = json.loads(
                (PACKAGE_BIN / "provenance.json").read_text(encoding="utf-8")
            )
            if not isinstance(provenance, dict) or (
                provenance.get("source_commit") != SOURCE_COMMIT
                or provenance.get("rust_package_version") != RUST_VERSION
                or provenance.get("upstream_version") != REFERENCE_VERSION
            ):
                raise ValueError("unexpected upstream identity")
            if hashlib.sha256(executable.read_bytes()).hexdigest() != provenance.get(
                "sha256"
            ):
                raise ValueError("executable checksum mismatch")
            schema = bundled_schema_path()
            if hashlib.sha256(schema.read_bytes()).hexdigest() != provenance.get(
                "schema_sha256"
            ):
                raise ValueError("schema checksum mismatch")
            if not os.access(executable, os.X_OK):
                raise ValueError("executable permission is missing")
        except (OSError, ValueError) as exc:
            raise NativeValidatorError(
                f"The bundled Rust validator is damaged: {exc}. Reinstall npdb."
            ) from exc
        return str(executable)

    root = source_root()
    if root is None:
        raise NativeValidatorError(
            "The installed validator bundle is missing; reinstall npdb."
        )
    candidates = [
        shutil.which(name),
        str(Path(sys.executable).parent / name),
        str(Path(sys.executable).parent / "Scripts" / name),
        str(root / "tools/bids-validator-rust/target/release" / name),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            result = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, check=False
            )
            expected = (
                f"bids-validator {REFERENCE_VERSION} commit {REFERENCE_COMMIT[:7]} "
                f"rust {RUST_VERSION}"
            )
            lines = result.stdout.splitlines()
            if (
                result.returncode != 0
                or not lines
                or lines[0] != expected
                or f"rust git commit {SOURCE_COMMIT}" not in lines
            ):
                raise NativeValidatorError(
                    f"Unexpected validator identity at {candidate}: "
                    f"{result.stdout or result.stderr}. Expected {expected}."
                )
            return candidate
    raise NativeValidatorError(
        "The pinned Rust BIDS validator is required for source/development installs. "
        "Run bash scripts/install_bids_validator.sh, or install a prebuilt npdb wheel."
    )


def validator_status() -> str:
    """Diagnostic status that keeps damaged/missing bundle errors visible."""
    try:
        validator_executable()
    except (OSError, RuntimeError) as exc:
        return f"unavailable ({exc})"
    return "bundled" if PACKAGE_BIN.exists() else "source/development"
