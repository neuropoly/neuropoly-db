"""Locate and verify npdb's bundled native validator, without provider imports."""

import hashlib
import json
import os
import re
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
        provenance = json.loads(
            (directory / "provenance.json").read_text(encoding="utf-8")
        )
        if not isinstance(provenance, dict):
            raise ValueError("invalid bundle provenance")
        if provenance.get("acquisition") == "github":
            identity = provenance
            if (
                identity.get("repository") != "https://github.com/rordenlab/bids-validator-rs"
                or not re.fullmatch(r"[0-9a-f]{40}", identity.get("source_commit", ""))
                or not re.fullmatch(r"[0-9a-f]{40}", identity.get("upstream_commit", ""))
                or any(not isinstance(identity.get(key), str) or not identity[key]
                       for key in ("rust_package_version", "upstream_version",
                                   "schema_version", "bids_version", "release_tag"))
            ):
                raise ValueError("invalid GitHub release identity")
        else:
            identity = source_identity()
        manifest_path = directory / "identity.json"
        if manifest_path.exists() or provenance.get("acquisition") == "github":
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest.get("files")
            actual = {path.name for path in directory.iterdir()
                      if path.name not in {"identity.json", "provenance.json"}}
            if (manifest.get("identity") != provenance or not isinstance(files, dict)
                    or set(files) != actual):
                raise ValueError("bundle identity or file inventory does not match")
            for name, digest in files.items():
                path = directory / name
                if (path.is_symlink() or not path.is_file()
                        or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
                    raise ValueError(f"{name} checksum does not match bundle identity")
        if any(
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
        embedded_commit = identity["source_commit"]
        if provenance.get("acquisition") == "appveyor":
            manifest = json.loads(
                Path(__file__).with_name("validator-appveyor.json").read_text(encoding="utf-8")
            )
            if not isinstance(manifest, dict) or any(manifest.get(key) != identity[key]
                   for key in ("source_commit", "source_tree_sha256")):
                raise ValueError("artifact approval does not match the pinned source")
            target = provenance.get("target")
            if not isinstance(target, str):
                raise ValueError("artifact target is missing or invalid")
            approved = manifest.get("artifacts", {}).get(target, [])
            if not any(
                item["archive_sha256"] == provenance.get("archive_sha256")
                and item["executable_sha256"] == provenance["sha256"]
                for item in approved
            ):
                raise ValueError("executable is not an approved AppVeyor artifact")
            embedded_commit = identity["source_commit"][:12]
            if provenance.get("embedded_source_commit") != embedded_commit:
                raise ValueError("artifact embedded commit does not match approval")
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
        reported_commit = re.search(r"^rust git commit ([0-9a-f]+)$", version, re.MULTILINE)
        if provenance.get("acquisition") == "github":
            embedded_commit = provenance.get("embedded_source_commit")
        if expected not in version or not reported_commit or reported_commit[1] != embedded_commit:
            raise ValueError("executable version does not match source identity")
        return executable
    except (OSError, ValueError, AttributeError, TypeError, subprocess.SubprocessError) as exc:
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
        "The native Rust BIDS validator is required. Run "
        "bash scripts/install_bids_validator.sh (downloads a GitHub release by default)."
    )
