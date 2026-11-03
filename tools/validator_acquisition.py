"""Shared, explicit native-validator acquisition settings and bounded transfers."""

import argparse
import hashlib
import json
import os
import platform
import re
import tempfile
import urllib.request
from pathlib import Path

SOURCES = ("github", "appveyor", "local")
MAX_DOWNLOAD = 32 * 1024 * 1024


def configuration(
    source: str | None = None,
    version: str | None = None,
    runtime: Path | str | None = None,
) -> dict[str, str]:
    if "NPDB_VALIDATOR_SOURCE_ONLY" in os.environ:
        raise RuntimeError(
            "NPDB_VALIDATOR_SOURCE_ONLY was removed; use NPDB_VALIDATOR_SOURCE=local."
        )

    source = (
        source
        if source is not None
        else os.environ.get("NPDB_VALIDATOR_SOURCE", "github")
    )
    version = (
        version
        if version is not None
        else os.environ.get("NPDB_VALIDATOR_VERSION") or None
    )
    runtime = (
        runtime
        if runtime is not None
        else os.environ.get("NPDB_WINDOWS_RUNTIME_DIR", "")
    )

    if source not in SOURCES:
        raise RuntimeError(
            f"Unknown validator source {source!r}; choose {', '.join(SOURCES)}."
        )

    if version is not None and source != "github":
        raise RuntimeError(
            "--validator-version/NPDB_VALIDATOR_VERSION requires the github source."
        )

    version = (
        version if version is not None else ("latest" if source == "github" else "")
    )
    if source == "github" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise RuntimeError(
            "Validator version must be 'latest' or a GitHub release tag."
        )

    runtime_directory = str(Path(runtime).expanduser().resolve()) if runtime else ""
    return {"source": source, "version": version, "runtime": runtime_directory}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--validator-source", choices=SOURCES)
    parser.add_argument(
        "--validator-version", help="GitHub release tag (default: latest stable)."
    )
    parser.add_argument(
        "--windows-runtime-dir",
        type=Path,
        help="Override automatic Microsoft CRT download with verified x64 "
        "runtime files and redistribution.json.",
    )


def environment(settings: dict[str, str]) -> dict[str, str]:
    result = {**os.environ, "NPDB_VALIDATOR_SOURCE": settings["source"]}
    result.pop("NPDB_VALIDATOR_VERSION", None)

    if settings["version"]:
        result["NPDB_VALIDATOR_VERSION"] = settings["version"]

    if settings["runtime"]:
        result["NPDB_WINDOWS_RUNTIME_DIR"] = settings["runtime"]

    return result


def host_target() -> str:
    machine = platform.machine().lower()
    architecture = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    suffix = {
        "Linux": "unknown-linux-gnu",
        "Darwin": "apple-darwin",
        "Windows": "pc-windows-msvc",
    }.get(platform.system())

    if suffix is None:
        raise RuntimeError(f"Unsupported validator host: {platform.system()}")

    return f"{architecture}-{suffix}"


def download(url: str, limit: int = MAX_DOWNLOAD) -> bytes:
    if not url.startswith("https://"):
        raise RuntimeError("Validator transfers require HTTPS.")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "npdb-wheel-builder",
            "Accept": "application/vnd.github+json",
        },
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)

    if len(data) > limit:
        raise RuntimeError(f"Download exceeds size limit: {url}")

    return data


def cached(root: Path, url: str, digest: str, namespace: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError(f"Missing/invalid SHA-256 for {url}")

    cache = Path(os.environ.get("NPDB_VALIDATOR_CACHE", root / "build/artifact-cache"))
    if cache.resolve().is_relative_to((root / "tools/bids-validator-rust").resolve()):
        raise RuntimeError("Artifact cache must be outside the validator submodule.")

    directory = cache / namespace
    path = directory / digest
    if path.exists() and path.stat().st_size > MAX_DOWNLOAD:
        raise RuntimeError(f"Cached download exceeds size limit: {path}")

    data = path.read_bytes() if path.exists() else download(url)
    if hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError(f"Download checksum mismatch: {url}")

    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.close()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)

    return data


def write_manifest(directory: Path, identity: dict) -> None:
    files = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.iterdir()
        if path.is_file() and path.name not in {"identity.json", "provenance.json"}
    }

    (directory / "identity.json").write_text(
        json.dumps({"identity": identity, "files": files}, indent=2) + "\n",
        encoding="utf-8",
    )
