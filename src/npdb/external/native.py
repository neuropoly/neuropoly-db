"""Locate and verify npdb's pinned native validator, without provider imports."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def source_identity() -> dict[str, str]:
    try:
        identity = json.loads(
            Path(__file__).with_name("validator-source.json").read_text(encoding="utf-8")
        )
        required = {
            "repository", "source_commit", "source_tree_sha256", "rust_package_version",
            "upstream_version", "upstream_commit", "schema_version", "bids_version",
        }
        if (
            not isinstance(identity, dict) or not required <= identity.keys()
            or any(not isinstance(key, str) or not isinstance(value, str) or not value
                   for key, value in identity.items())
        ):
            raise ValueError("invalid pinned source identity")
        return identity
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read npdb native validator source identity: {exc}") from exc


def verify_bundle(directory: Path) -> Path:
    """Reject incomplete, altered or mismatched bundles before executing them."""
    try:
        identity = source_identity()
        provenance = json.loads(
            (directory / "provenance.json").read_text(encoding="utf-8")
        )
        if not isinstance(provenance, dict) or any(
            provenance.get(key) != value for key, value in identity.items()
        ):
            raise ValueError("source identity does not match the pinned validator")
        executable = directory / (
            "bids-validator.exe" if os.name == "nt" else "bids-validator"
        )
        for path, key in (
            (executable, "sha256"),
            (directory / "schema.json", "schema_sha256"),
        ):
            if hashlib.sha256(path.read_bytes()).hexdigest() != provenance.get(key):
                raise ValueError(f"{path.name} checksum does not match provenance")
        for notice in ("LICENSE", "THIRD-PARTY-LICENSES"):
            if not (directory / notice).is_file():
                raise ValueError(f"missing {notice}")
        if not os.access(executable, os.X_OK):
            raise ValueError("validator is not executable")
        version = subprocess.run(
            [str(executable), "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        expected = (
            f"{identity['upstream_version']} commit {identity['upstream_commit'][:7]} "
            f"rust {identity['rust_package_version']}"
        )
        if expected not in version or (
            f"rust git commit {identity['source_commit']}" not in version
        ):
            raise ValueError("executable version does not match source identity")
        return executable
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Invalid native validator bundle at {directory}: {exc}") from exc


def validator_executable() -> str:
    package = Path(__file__).resolve().parents[1]
    bundle = package / "_bin"
    if bundle.exists():
        return str(verify_bundle(bundle))
    # Only an actual source checkout may use the explicit development installer.
    source = package.parent
    if source.name != "src" or not (source.parent / "pyproject.toml").is_file():
        raise RuntimeError(
            "The installed npdb native validator bundle is missing. Reinstall "
            "a platform wheel; an external validator cannot replace a damaged bundle."
        )
    name = "bids-validator.exe" if os.name == "nt" else "bids-validator"
    candidates = [Path(sys.executable).parent / name]
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    for candidate in dict.fromkeys(candidates):
        if candidate.is_file():
            return str(verify_bundle(candidate.parent / "npdb-validator"))
    raise RuntimeError(
        "The pinned Rust BIDS validator is required. Run "
        "bash scripts/install_bids_validator.sh after installing Rust 1.90+."
    )
