import hashlib
import importlib.util
import io
import json
import runpy
import subprocess
import tarfile
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from npdb.external.native import verify_bundle

ROOT = Path(__file__).resolve().parents[1]
COMMON = runpy.run_path(str(ROOT / "tools/validator_acquisition.py"))
SPEC = importlib.util.spec_from_file_location("validator_github", ROOT / "tools/validator_github.py")
assert SPEC is not None and SPEC.loader is not None
GITHUB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GITHUB)
TAG = "v0.1.20261031"
COMMIT = "a" * 40
TARGET = "x86_64-unknown-linux-gnu"


@pytest.fixture(autouse=True)
def isolate_selection(monkeypatch, tmp_path):
    for name in ("NPDB_VALIDATOR_SOURCE", "NPDB_VALIDATOR_VERSION", "NPDB_VALIDATOR_RELEASE",
                 "NPDB_VALIDATOR_SOURCE_ONLY", "NPDB_WINDOWS_RUNTIME_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NPDB_VALIDATOR_CACHE", str(tmp_path / "cache"))
    download = Mock(side_effect=AssertionError("Unexpected network"))
    monkeypatch.setitem(GITHUB.COMMON, "download", download)
    monkeypatch.setitem(GITHUB.COMMON["cached"].__globals__, "download", download)


@pytest.fixture
def release(monkeypatch):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("bids-validator", b"fixture executable")
    binary_archive = data.getvalue()
    record = {
        "tag": TAG, "commit": COMMIT,
        "assets": [{
            "name": "bids-validator_lnx.zip",
            "browser_download_url": f"{GITHUB.REPOSITORY}/releases/download/{TAG}/bids-validator_lnx.zip",
            "digest": "sha256:" + hashlib.sha256(binary_archive).hexdigest(),
        }],
    }
    monkeypatch.setenv("NPDB_VALIDATOR_RELEASE", json.dumps(record))
    GITHUB.COMMON["download"].side_effect = None
    GITHUB.COMMON["download"].return_value = binary_archive
    files = {
        "Cargo.toml": b"""
[package]
version = "0.1.20261031"
[package.metadata.bids-validator]
upstream_version = "3.0.2"
upstream_commit = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
""",
        "upstream.lock.json": json.dumps({
            "bids_validator_version": "3.0.2", "bids_validator_commit": "b" * 40,
        }).encode(),
        "src/main.rs": b"",
        "crates/bids-schema-codegen/src/lib.rs": b'include_str!("schema-1.2.7.json")',
        "schemas/schema-1.2.7.json": b'{"bids_version":"1.11.1","schema_version":"1.2.7"}',
        "Cargo.lock": b"package = []",
        "LICENSE": b"fixture license",
    }
    monkeypatch.setattr(GITHUB, "source_file", lambda commit, path: files[path])
    return record


def test_default_and_explicit_configuration(monkeypatch):
    assert COMMON["configuration"]() == {"source": "github", "version": "latest", "runtime": ""}
    monkeypatch.setenv("NPDB_VALIDATOR_VERSION", TAG)
    assert COMMON["configuration"]()["version"] == TAG
    assert COMMON["configuration"](version="v1")["version"] == "v1"
    with pytest.raises(RuntimeError, match="requires the github"):
        COMMON["configuration"]("local")


@pytest.mark.parametrize("version", ["../main", "", "a/b", "--flag"])
def test_invalid_release_selection_fails(version):
    with pytest.raises(RuntimeError, match="release tag"):
        COMMON["configuration"](version=version)


def test_cli_source_overrides_environment(monkeypatch):
    monkeypatch.setenv("NPDB_VALIDATOR_SOURCE", "local")
    assert COMMON["configuration"]("github", TAG)["source"] == "github"


def test_release_bundle_uses_matching_identity_schema_and_notices(release, tmp_path):
    executable, identity = GITHUB.acquire(ROOT, tmp_path / "bundle", TARGET, TAG)
    assert identity["source_commit"] == COMMIT
    assert identity["schema_version"] == "1.2.7"
    assert identity["upstream_version"] == "3.0.2"
    assert executable.read_bytes() == b"fixture executable"
    assert "BIDS schema 1.2.7" in (executable.parent / "THIRD-PARTY-LICENSES").read_text()
    COMMON["write_manifest"](executable.parent, identity)
    version = f"3.0.2 commit bbbbbbb rust 0.1.20261031\nrust git commit {COMMIT[:12]}\n"
    with (
        patch("npdb.external.native.subprocess.run",
              return_value=subprocess.CompletedProcess([], 0, stdout=version)),
        patch("npdb.external.native.source_identity",
              side_effect=AssertionError("GitHub bundles must not use the local source pin")),
    ):
        assert verify_bundle(executable.parent) == executable
        (executable.parent / "THIRD-PARTY-LICENSES").write_text("tampered")
        with pytest.raises(RuntimeError, match="checksum"):
            verify_bundle(executable.parent)


def test_missing_asset_fails_without_fallback(release):
    with pytest.raises(RuntimeError, match="no supported asset"):
        GITHUB.release_binary(ROOT, "aarch64-unknown-linux-gnu", TAG)
    GITHUB.COMMON["download"].assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("digest", None), ("digest", "sha256:" + "0" * 64),
    ("browser_download_url", "https://example.invalid/replacement.zip"),
])
def test_untrusted_or_changed_assets_fail(release, monkeypatch, field, value):
    release["assets"][0][field] = value
    monkeypatch.setenv("NPDB_VALIDATOR_RELEASE", json.dumps(release))
    with pytest.raises(RuntimeError, match="SHA-256|checksum"):
        GITHUB.release_binary(ROOT, TARGET, TAG)


def test_cache_corruption_is_not_redownloaded(release, tmp_path):
    GITHUB.release_binary(ROOT, TARGET, TAG)
    path, = (tmp_path / "cache/github").iterdir()
    path.write_bytes(b"corrupt")
    GITHUB.COMMON["download"].reset_mock()
    with pytest.raises(RuntimeError, match="checksum"):
        GITHUB.release_binary(ROOT, TARGET, TAG)
    GITHUB.COMMON["download"].assert_not_called()


def test_forwarded_release_cannot_override_explicit_tag(release):
    with pytest.raises(RuntimeError, match="conflicts"):
        GITHUB.resolve("v1")


@pytest.mark.parametrize("record", [None, {}, {"tag": TAG, "commit": None, "assets": []}])
def test_malformed_release_fails_explicitly(record):
    with pytest.raises(RuntimeError, match="Invalid resolved"):
        GITHUB.validate_record(record)


def test_latest_resolves_annotated_tag_once(monkeypatch):
    fetch = Mock(side_effect=[
        {"tag_name": TAG, "assets": []},
        {"object": {"type": "tag", "sha": "c" * 40}},
        {"object": {"type": "commit", "sha": COMMIT}},
    ])
    monkeypatch.setattr(GITHUB, "json_url", fetch)
    assert GITHUB.resolve("latest")["commit"] == COMMIT
    assert fetch.call_args_list[0].args[0].endswith("/releases/latest")
    assert fetch.call_count == 3


def test_failed_github_acquisition_never_compiles_or_replaces_bundle(monkeypatch, tmp_path):
    build = runpy.run_path(str(ROOT / "tools/build_validator.py"))
    real_run_path = runpy.run_path
    acquire = Mock(side_effect=RuntimeError("release unavailable"))

    def helpers(path, *args, **kwargs):
        if str(path).endswith("validator_github.py"):
            return {"acquire": acquire}
        return real_run_path(path, *args, **kwargs)

    monkeypatch.setattr(runpy, "run_path", helpers)
    compile_local = Mock(side_effect=AssertionError("No fallback"))
    monkeypatch.setitem(build["build_bundle"].__globals__, "compile_bundle", compile_local)
    destination = tmp_path / "bundle"
    destination.mkdir()
    (destination / "old").write_text("preserved")
    with pytest.raises(RuntimeError, match="release unavailable"):
        build["build_bundle"](ROOT, destination, TARGET)
    compile_local.assert_not_called()
    assert (destination / "old").read_text() == "preserved"


@pytest.mark.parametrize("required,expected", [
    ("GLIBC_2.2.5 GLIBC_2.17", (2, 17)),
    ("GLIBC_2.14 GLIBC_2.30", (2, 30)),
    ("GLIBC_2.34 GLIBC_2.28", (2, 34)),
])
def test_linux_tag_uses_highest_required_symbol(required, expected):
    audit = runpy.run_path(str(ROOT / "tools/audit_validator.py"))
    with patch("subprocess.run", return_value=Mock(stdout=required)):
        assert audit["linux_baseline"](Path("validator")) == expected


def test_missing_crate_license_uses_checksum_bound_vcs_revision(monkeypatch):
    data = io.BytesIO()
    files = {
        "fixture-1.0/Cargo.toml": b'[package]\nrepository = "https://github.com/example/crate"\n',
        "fixture-1.0/.cargo_vcs_info.json": json.dumps({"git": {"sha1": COMMIT}}).encode(),
    }
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    monkeypatch.setitem(GITHUB.COMMON, "cached", lambda *args: data.getvalue())
    download = GITHUB.COMMON["download"]
    download.side_effect = [
        b"fixture complete license",
        urllib.error.HTTPError("url", 404, "absent", {}, None),
        urllib.error.HTTPError("url", 404, "absent", {}, None),
    ]
    result = GITHUB.notices(ROOT, COMMIT, {"package": [{
        "name": "fixture", "version": "1.0", "checksum": "1" * 64,
        "source": "registry+https://github.com/rust-lang/crates.io-index",
    }]}, "1.2.7", "1.11.1")
    assert "fixture complete license" in result
    for call in download.call_args_list:
        assert f"https://raw.githubusercontent.com/example/crate/{COMMIT}/" in call.args[0]


def test_unrecognized_glibc_abi_does_not_get_a_lower_tag():
    audit = runpy.run_path(str(ROOT / "tools/audit_validator.py"))
    with patch("subprocess.run", return_value=Mock(stdout="GLIBC_ABI_DT_RELR")):
        with pytest.raises(RuntimeError, match="nonnumeric"):
            audit["linux_baseline"](Path("validator"))
