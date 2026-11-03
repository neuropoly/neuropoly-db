"""Acquire checksum-pinned AppVeyor executables without a Rust toolchain."""

import argparse
import gzip
import hashlib
import http.client
import io
import json
import os
import runpy
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

MAX_ARCHIVE = 16 * 1024 * 1024
MAX_EXECUTABLE = 32 * 1024 * 1024


def approved_manifest(root: Path, identity: dict) -> dict | None:
    path = root / "src/npdb/external/validator-appveyor.json"
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError("Invalid validator artifact approval manifest.")
    if any(manifest.get(key) != identity[key]
           for key in ("source_commit", "source_tree_sha256")):
        return None
    return manifest


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "npdb-wheel-builder"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE:
        raise RuntimeError("AppVeyor archive exceeds the download size limit.")
    return data


def acquire(root: Path, destination: Path, target: str) -> tuple[Path, dict]:
    identity = runpy.run_path(str(root / "src/npdb/external/native.py"))["source_identity"]()
    manifest = approved_manifest(root, identity)
    records = manifest.get("artifacts", {}).get(target, []) if manifest else []
    if not records:
        raise RuntimeError(f"No approved AppVeyor artifact for {target}; select another source explicitly.")
    cache = Path(os.environ.get("NPDB_VALIDATOR_CACHE", root / "build/artifact-cache"))
    if cache.resolve().is_relative_to((root / "tools/bids-validator-rust").resolve()):
        raise RuntimeError("Artifact cache must be outside the validator submodule.")
    for record in records:
        digest = record["archive_sha256"]
        archive = cache / f"{digest}.zip"
        url = (
            f"https://ci.appveyor.com/api/buildjobs/{record['job']}/artifacts/"
            f"{record['file']}"
        )
        if archive.is_file():
            if archive.stat().st_size > MAX_ARCHIVE:
                raise RuntimeError("Cached AppVeyor archive exceeds the download size limit.")
            data = archive.read_bytes()
        else:
            try:
                data = download(url)
            except (
                urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException, ssl.SSLError,
            ) as error:
                print(f"AppVeyor artifact unavailable for {target}: {error}", file=sys.stderr)
                continue
        if hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError(f"AppVeyor archive checksum mismatch for {target}: {url}")
        if not archive.is_file():
            cache.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temporary:
                cached = Path(temporary.name)
                try:
                    temporary.write(data)
                    temporary.close()
                    cached.replace(archive)
                finally:
                    cached.unlink(missing_ok=True)
        name = "bids-validator.exe" if "windows" in target else "bids-validator"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zipped:
                entries = zipped.infolist()
                if (
                    len(entries) != 1 or entries[0].filename != name
                    or entries[0].file_size > MAX_EXECUTABLE
                ):
                    raise RuntimeError("Expected one bounded validator executable in archive.")
                executable_data = zipped.read(entries[0])
        except (zipfile.BadZipFile, RuntimeError) as error:
            raise RuntimeError(f"Invalid approved AppVeyor archive: {error}") from error
        if hashlib.sha256(executable_data).hexdigest() != record["executable_sha256"]:
            raise RuntimeError(f"AppVeyor executable checksum mismatch for {target}.")
        notices = root / "src/npdb/external/validator-appveyor-notices.txt.gz"
        compressed = notices.read_bytes()
        if hashlib.sha256(compressed).hexdigest() != manifest["notices_sha256"]:
            raise RuntimeError("Pinned artifact license notices checksum mismatch.")
        destination.mkdir(parents=True, exist_ok=True)
        executable = destination / name
        executable.write_bytes(executable_data)
        executable.chmod(0o755)
        (destination / "THIRD-PARTY-LICENSES").write_bytes(gzip.decompress(compressed))
        print(f"Using approved AppVeyor artifact for {target}: {url}", flush=True)
        return executable, {
            "acquisition": "appveyor", "artifact_url": url,
            "archive_sha256": digest,
            "embedded_source_commit": identity["source_commit"][:12],
        }
    raise RuntimeError(f"No usable AppVeyor artifact for {target}; select another source explicitly.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-notices", type=Path, required=True,
                        help="Generate the pinned notice snapshot from Cargo metadata JSON.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    packages = json.loads(args.write_notices.read_text(encoding="utf-8"))["packages"]
    text = runpy.run_path(str(root / "tools/build_validator.py"))["third_party_notices"](packages)
    compressed = gzip.compress(text.encode("utf-8"), mtime=0)
    output = root / "src/npdb/external/validator-appveyor-notices.txt.gz"
    output.write_bytes(compressed)
    print(f"{output}: sha256 {hashlib.sha256(compressed).hexdigest()}")
