"""Acquire an official release and its matching schema and license sources."""

import hashlib
import io
import json
import os
import re
import runpy
import tarfile
import tomllib
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path

COMMON = runpy.run_path(str(Path(__file__).with_name("validator_acquisition.py")))
API = "https://api.github.com/repos/rordenlab/bids-validator-rs"
REPOSITORY = "https://github.com/rordenlab/bids-validator-rs"
ASSETS = {
    "x86_64-unknown-linux-gnu": "bids-validator_lnx.zip",
    "x86_64-apple-darwin": "bids-validator_mac.zip",
    "aarch64-apple-darwin": "bids-validator_mac.zip",
    "x86_64-pc-windows-msvc": "bids-validator_win.zip",
}


def json_url(url: str) -> dict:
    try:
        value = json.loads(COMMON["download"](url))
    except ValueError as error:
        raise RuntimeError(f"Invalid GitHub response from {url}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object from {url}")
    return value


def resolve(version: str) -> dict:
    forwarded = os.environ.get("NPDB_VALIDATOR_RELEASE")
    if forwarded:
        try:
            record = json.loads(forwarded)
        except ValueError as error:
            raise RuntimeError("Invalid forwarded GitHub validator release JSON.") from error
        validate_record(record)
        if version not in {"latest", record["tag"]}:
            raise RuntimeError("Forwarded validator release conflicts with requested version.")
        return record
    endpoint = "latest" if version == "latest" else "tags/" + urllib.parse.quote(version, safe="")
    release = json_url(f"{API}/releases/{endpoint}")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", tag):
        raise RuntimeError("GitHub release has an invalid tag.")
    if release.get("draft") or (version == "latest" and release.get("prerelease")):
        raise RuntimeError("Latest validator must be a published stable release.")
    if version != "latest" and version != tag:
        raise RuntimeError("GitHub returned a different validator release tag.")
    reference = json_url(f"{API}/git/ref/tags/{urllib.parse.quote(tag, safe='')}")
    obj = reference.get("object", {})
    for _ in range(8):
        if not isinstance(obj, dict):
            raise RuntimeError("Invalid validator tag reference.")
        if obj.get("type") == "commit":
            break
        if obj.get("type") != "tag" or not re.fullmatch(r"[0-9a-f]{40}", obj.get("sha", "")):
            raise RuntimeError("Cannot resolve validator tag to a commit.")
        obj = json_url(f"{API}/git/tags/{obj['sha']}").get("object", {})
    record = {"tag": tag, "commit": obj.get("sha"), "assets": release.get("assets")}
    validate_record(record)
    if obj.get("type") != "commit":
        raise RuntimeError("Validator tag chain is too deep.")
    return record


def validate_record(record: dict) -> None:
    if (not isinstance(record, dict)
            or not isinstance(record.get("tag"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", record["tag"])
            or not isinstance(record.get("commit"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", record.get("commit", ""))
            or not isinstance(record.get("assets"), list)):
        raise RuntimeError("Invalid resolved GitHub validator release.")


def source_file(commit: str, path: str) -> bytes:
    return COMMON["download"](
        f"https://raw.githubusercontent.com/rordenlab/bids-validator-rs/{commit}/{path}"
    )


def notices(root: Path, commit: str, lock: dict, schema_version: str,
            bids_version: str) -> str:
    sections = [
        f"BIDS schema {schema_version} (BIDS {bids_version}): CC-BY-4.0\n"
        "https://creativecommons.org/licenses/by/4.0/\n"
        f"Source: {REPOSITORY}/tree/{commit}/schemas\n"
    ]
    for package in lock["package"]:
        name, version = package["name"], package["version"]
        source = package.get("source")
        if source is None:
            continue
        if source != "registry+https://github.com/rust-lang/crates.io-index":
            raise RuntimeError(f"Unsupported license source for {name} {version}: {source}")
        data = COMMON["cached"](
            root, f"https://static.crates.io/crates/{name}/{name}-{version}.crate",
            package.get("checksum", ""), "crate-licenses",
        )
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            licenses = []
            total = 0
            for entry in archive:
                relative = Path(entry.name)
                if (relative.is_absolute() or ".." in relative.parts
                        or entry.issym() or entry.islnk()):
                    raise RuntimeError(f"Unsafe license archive for {name}")
                if not entry.isfile() or not relative.name.upper().startswith(
                    ("LICENSE", "COPYING", "NOTICE", "AUTHORS", "COPYRIGHT")
                ):
                    continue
                total += entry.size
                if total > 2 * 1024 * 1024:
                    raise RuntimeError(f"License text exceeds size limit for {name}")
                stream = archive.extractfile(entry)
                if stream is None:
                    raise RuntimeError(f"Cannot read license for {name}")
                licenses.append(stream.read().decode("utf-8"))
            if not licenses:
                # Some workspace crates omit root licenses from their published tarball.
                prefix = f"{name}-{version}/"
                cargo_file = archive.extractfile(prefix + "Cargo.toml")
                vcs_file = archive.extractfile(prefix + ".cargo_vcs_info.json")
                if cargo_file is None or vcs_file is None:
                    raise RuntimeError(f"No license source metadata for {name} {version}")
                cargo = tomllib.loads(cargo_file.read().decode())
                vcs = json.loads(vcs_file.read())
                repository = cargo["package"].get("repository", "").removesuffix(".git")
                revision = vcs.get("git", {}).get("sha1", "")
                if (not re.fullmatch(r"https://github.com/[\w.-]+/[\w.-]+", repository)
                        or not re.fullmatch(r"[0-9a-f]{40}", revision)):
                    raise RuntimeError(f"Unsupported missing-license source for {name}")
                base = repository.replace("https://github.com/", "https://raw.githubusercontent.com/")
                for filename in ("LICENSE", "LICENSE-MIT", "LICENSE-APACHE"):
                    try:
                        licenses.append(COMMON["download"](
                            f"{base}/{revision}/{filename}", 2 * 1024 * 1024
                        ).decode("utf-8"))
                    except urllib.error.HTTPError as error:
                        if error.code != 404:
                            raise
        if not licenses:
            raise RuntimeError(f"No redistributable license text found for {name} {version}")
        sections.append(f"\n=== {name} {version} ===\n" + "\n".join(licenses))
    return "\n".join(sections)


def release_binary(root: Path, target: str, version: str) -> tuple[dict, str, str, bytes]:
    record = resolve(version)
    name = ASSETS.get(target)
    if name is None:
        raise RuntimeError(
            f"GitHub release {record['tag']} has no supported asset for {target}; "
            "select --validator-source local explicitly."
        )
    matches = [asset for asset in record["assets"]
               if isinstance(asset, dict) and asset.get("name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"GitHub release {record['tag']} must contain exactly one {name}.")
    asset = matches[0]
    url = asset.get("browser_download_url", "")
    expected_url = f"{REPOSITORY}/releases/download/{record['tag']}/{name}"
    digest = asset.get("digest", "")
    if (url != expected_url or not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)):
        raise RuntimeError(f"Missing trusted URL/SHA-256 for GitHub asset {name}.")
    data = COMMON["cached"](root, url, digest.removeprefix("sha256:"), "github")
    executable_name = "bids-validator.exe" if "windows" in target else "bids-validator"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if (len(entries) != 1 or entries[0].filename != executable_name
                or entries[0].file_size > 32 * 1024 * 1024):
            raise RuntimeError("Expected one bounded validator executable in release ZIP.")
        binary = archive.read(entries[0])
    return record, url, digest.removeprefix("sha256:"), binary


def acquire(root: Path, destination: Path, target: str, version: str) -> tuple[Path, dict]:
    try:
        return _acquire(root, destination, target, version)
    except (KeyError, TypeError, ValueError, zipfile.BadZipFile, tarfile.TarError) as error:
        raise RuntimeError(f"Invalid GitHub validator release {version}: {error}") from error


def _acquire(root: Path, destination: Path, target: str, version: str) -> tuple[Path, dict]:
    record, url, digest, binary = release_binary(root, target, version)
    executable_name = "bids-validator.exe" if "windows" in target else "bids-validator"
    commit = record["commit"]
    cargo = tomllib.loads(source_file(commit, "Cargo.toml").decode())
    upstream = json.loads(source_file(commit, "upstream.lock.json"))
    metadata = cargo["package"]["metadata"]["bids-validator"]
    if (metadata["upstream_version"] != upstream["bids_validator_version"]
            or metadata["upstream_commit"] != upstream["bids_validator_commit"]):
        raise RuntimeError("Release upstream identities disagree.")
    # The source records which embedded schema the engine actually uses.
    code = source_file(commit, "src/main.rs").decode()
    schemas = set(re.findall(r"schema-(\d+\.\d+\.\d+)\.json", code))
    if not schemas:
        code = source_file(commit, "crates/bids-schema-codegen/src/lib.rs").decode()
        schemas = set(re.findall(r"schema-(\d+\.\d+\.\d+)\.json", code))
    if len(schemas) != 1:
        raise RuntimeError("Cannot identify one embedded schema in the release source.")
    schema_version, = schemas
    schema = source_file(commit, f"schemas/schema-{schema_version}.json")
    schema_data = json.loads(schema)
    bids_version = schema_data.get("bids_version")
    if not isinstance(bids_version, str) or schema_data.get("schema_version") != schema_version:
        raise RuntimeError("Release schema has missing or inconsistent version metadata.")
    lock = tomllib.loads(source_file(commit, "Cargo.lock").decode())
    license_text = source_file(commit, "LICENSE")
    third_party = notices(root, commit, lock, schema_version, bids_version)
    identity = {
        "repository": REPOSITORY, "source_commit": commit,
        "rust_package_version": cargo["package"]["version"],
        "upstream_version": upstream["bids_validator_version"],
        "upstream_commit": upstream["bids_validator_commit"],
        "schema_version": schema_version, "bids_version": bids_version,
        "release_tag": record["tag"], "acquisition": "github", "target": target,
        "artifact_url": url, "archive_sha256": digest,
        "embedded_source_commit": commit[:12],
    }
    destination.mkdir(parents=True, exist_ok=True)
    executable = destination / executable_name
    executable.write_bytes(binary)
    executable.chmod(0o755)
    (destination / "schema.json").write_bytes(schema)
    (destination / "LICENSE").write_bytes(license_text)
    (destination / "THIRD-PARTY-LICENSES").write_text(third_party, encoding="utf-8")
    identity.update(sha256=hashlib.sha256(binary).hexdigest(),
                    schema_sha256=hashlib.sha256(schema).hexdigest())
    (destination / "provenance.json").write_text(
        json.dumps(identity, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Using GitHub validator {record['tag']} ({commit}) for {target}", flush=True)
    return executable, identity
