import gzip
import hashlib
import http.client
import importlib.util
import io
import json
import runpy
import shutil
import ssl
import struct
import subprocess
import tomllib
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from npdb.external.native import source_identity, verify_bundle

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validator_artifacts", ROOT / "tools/validator_artifacts.py")
assert SPEC is not None and SPEC.loader is not None
ARTIFACTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARTIFACTS)
AUDIT = runpy.run_path(str(ROOT / "tools/audit_validator.py"))
BUILD = runpy.run_path(str(ROOT / "tools/build_validator.py"))
TARGET = "aarch64-apple-darwin"


def macho(cpu=0x0100000C, minimum=11 << 16, library="/usr/lib/libSystem.B.dylib"):
    name = library.encode() + b"\0"
    length = (24 + len(name) + 7) // 8 * 8
    commands = struct.pack("<IIIIII", 0x32, 24, 1, minimum, 0, 0)
    commands += struct.pack("<IIIIII", 0xC, length, 24, 0, 0, 0) + name.ljust(length - 24, b"\0")
    return struct.pack("<IIIIIIII", 0xFEEDFACF, cpu, 0, 2, 2, len(commands), 0, 0) + commands


def archive(data, name="bids-validator"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr(name, data)
    return output.getvalue()


@pytest.fixture
def artifact_root(tmp_path, monkeypatch):
    root = tmp_path / "project"
    external = root / "src/npdb/external"
    external.mkdir(parents=True)
    for name in ("native.py", "validator-source.json"):
        shutil.copy2(ROOT / "src/npdb/external" / name, external / name)
    (root / "tools").mkdir()
    shutil.copy2(ROOT / "tools/audit_validator.py", root / "tools/audit_validator.py")
    shutil.copy2(ROOT / "tools/validator_artifacts.py", root / "tools/validator_artifacts.py")
    source = root / "tools/bids-validator-rust"
    (source / "schemas").mkdir(parents=True)
    (source / "LICENSE").write_text("fixture license")
    (source / "schemas/schema-1.2.1.json").write_text("{}")
    compressed = gzip.compress(b"complete fixture notices", mtime=0)
    (external / "validator-artifact-notices.txt.gz").write_bytes(compressed)
    binary = macho()
    data = archive(binary)
    manifest = {
        **{key: source_identity()[key] for key in ("source_commit", "source_tree_sha256")},
        "notices_sha256": hashlib.sha256(compressed).hexdigest(),
        "artifacts": {TARGET: [{
            "job": "fixture-job", "file": "bids-validator_mac.zip",
            "archive_sha256": hashlib.sha256(data).hexdigest(),
            "executable_sha256": hashlib.sha256(binary).hexdigest(),
        }]},
    }
    (external / "validator-artifacts.json").write_text(json.dumps(manifest))
    monkeypatch.delenv("NPDB_VALIDATOR_SOURCE_ONLY", raising=False)
    monkeypatch.setenv("NPDB_VALIDATOR_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ARTIFACTS, "download", Mock(return_value=data))
    return root, manifest, data


def save_manifest(root, manifest):
    (root / "src/npdb/external/validator-artifacts.json").write_text(json.dumps(manifest))


def test_artifact_is_audited_cached_and_includes_notices(artifact_root, tmp_path):
    root, manifest, data = artifact_root
    binary, provenance = ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET)
    assert binary.read_bytes() == macho()
    assert binary.stat().st_mode & 0o111
    assert (binary.parent / "THIRD-PARTY-LICENSES").read_text() == "complete fixture notices"
    assert provenance["embedded_source_commit"] == source_identity()["source_commit"][:12]
    ARTIFACTS.download.assert_called_once()
    ARTIFACTS.download.side_effect = AssertionError("Cache hit must not access the network")
    ARTIFACTS.acquire(root, tmp_path / "second", TARGET)


@pytest.mark.parametrize("error", [
    urllib.error.HTTPError("url", 404, "expired", {}, None),
    urllib.error.HTTPError("url", 503, "unavailable", {}, None),
    urllib.error.URLError("offline"), TimeoutError("timeout"),
    http.client.IncompleteRead(b"truncated"), ssl.SSLError("transfer failed"),
])
def test_unavailable_artifact_reports_source_fallback(artifact_root, tmp_path, capsys, error):
    root, _, _ = artifact_root
    ARTIFACTS.download.side_effect = error
    assert ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET) is None
    assert "unavailable" in capsys.readouterr().err
    assert not (tmp_path / "bundle").exists()


def test_approved_alternative_job_is_tried_after_expiry(artifact_root, tmp_path):
    root, manifest, data = artifact_root
    manifest["artifacts"][TARGET].append({**manifest["artifacts"][TARGET][0], "job": "rebuilt"})
    save_manifest(root, manifest)
    ARTIFACTS.download.side_effect = [urllib.error.URLError("expired"), data]
    assert ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET) is not None
    assert "/rebuilt/" in ARTIFACTS.download.call_args.args[0]


@pytest.mark.parametrize("change", ["source_commit", "source_tree_sha256"])
def test_wrong_source_never_downloads(artifact_root, tmp_path, change):
    root, manifest, _ = artifact_root
    manifest[change] = "wrong"
    save_manifest(root, manifest)
    assert ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET) is None
    ARTIFACTS.download.assert_not_called()


def test_source_only_never_downloads(artifact_root, tmp_path, monkeypatch):
    root, _, _ = artifact_root
    monkeypatch.setenv("NPDB_VALIDATOR_SOURCE_ONLY", "1")
    assert ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET) is None
    ARTIFACTS.download.assert_not_called()


@pytest.mark.parametrize("field", ["archive_sha256", "executable_sha256"])
def test_checksum_mismatch_is_a_hard_failure(artifact_root, tmp_path, field):
    root, manifest, _ = artifact_root
    manifest["artifacts"][TARGET][0][field] = "0" * 64
    save_manifest(root, manifest)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET)


def test_corrupt_cache_is_not_silently_replaced(artifact_root, tmp_path):
    root, manifest, _ = artifact_root
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{manifest['artifacts'][TARGET][0]['archive_sha256']}.zip").write_bytes(b"bad")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET)
    ARTIFACTS.download.assert_not_called()


@pytest.mark.parametrize("payload", [
    b"not a zip", archive(macho(), "../bids-validator"), archive(macho(), "unexpected"),
])
def test_malformed_or_unsafe_archive_is_rejected(artifact_root, tmp_path, payload):
    root, manifest, _ = artifact_root
    manifest["artifacts"][TARGET][0]["archive_sha256"] = hashlib.sha256(payload).hexdigest()
    save_manifest(root, manifest)
    ARTIFACTS.download.return_value = payload
    with pytest.raises(RuntimeError, match="Invalid approved AppVeyor archive"):
        ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET)


def test_incompatible_binary_reports_fallback(artifact_root, tmp_path, capsys):
    root, manifest, _ = artifact_root
    binary = macho(minimum=12 << 16)
    data = archive(binary)
    manifest["artifacts"][TARGET][0].update(
        archive_sha256=hashlib.sha256(data).hexdigest(),
        executable_sha256=hashlib.sha256(binary).hexdigest(),
    )
    save_manifest(root, manifest)
    ARTIFACTS.download.return_value = data
    assert ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET) is None
    assert "incompatible" in capsys.readouterr().err


def test_notice_tampering_is_rejected(artifact_root, tmp_path):
    root, _, _ = artifact_root
    (root / "src/npdb/external/validator-artifact-notices.txt.gz").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="notices checksum mismatch"):
        ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET)


def test_executable_size_limit_is_enforced(artifact_root, tmp_path, monkeypatch):
    root, _, _ = artifact_root
    monkeypatch.setattr(ARTIFACTS, "MAX_EXECUTABLE", 1)
    with pytest.raises(RuntimeError, match="bounded validator"):
        ARTIFACTS.acquire(root, tmp_path / "bundle", TARGET)


def test_pinned_notices_cover_all_locked_packages():
    external = ROOT / "src/npdb/external"
    manifest = json.loads((external / "validator-artifacts.json").read_text())
    compressed = (external / "validator-artifact-notices.txt.gz").read_bytes()
    assert hashlib.sha256(compressed).hexdigest() == manifest["notices_sha256"]
    assert manifest["source_commit"] == source_identity()["source_commit"]
    assert manifest["source_tree_sha256"] == source_identity()["source_tree_sha256"]
    notices = gzip.decompress(compressed).decode()
    lock = tomllib.loads((ROOT / "tools/bids-validator-rust/Cargo.lock").read_text())
    for package in lock["package"]:
        assert f"=== {package['name']} {package['version']} (" in notices


def test_download_has_a_size_limit(monkeypatch):
    response = Mock()
    response.read.return_value = b"x" * (ARTIFACTS.MAX_ARCHIVE + 1)
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(ARTIFACTS.urllib.request, "urlopen", Mock(return_value=context))
    with pytest.raises(RuntimeError, match="size limit"):
        ARTIFACTS.download("https://ci.appveyor.com/test")


def test_bundle_acquisition_needs_no_cargo(artifact_root, tmp_path, monkeypatch):
    root, _, data = artifact_root
    monkeypatch.setitem(BUILD["build_bundle"].__globals__, "source_directory",
                        lambda root: root / "tools/bids-validator-rust")
    response = io.BytesIO(data)
    with (
        patch("urllib.request.urlopen", return_value=response),
        patch("shutil.which", side_effect=AssertionError("Must not require Cargo")),
        patch("subprocess.run", side_effect=AssertionError("Must not compile")),
    ):
        binary = BUILD["build_bundle"](root, tmp_path / "bundle", TARGET)
    provenance = json.loads((binary.parent / "provenance.json").read_text())
    assert provenance["source_commit"] == source_identity()["source_commit"]
    assert provenance["acquisition"] == "appveyor"
    assert (binary.parent / "schema.json").is_file()
    assert (binary.parent / "LICENSE").is_file()


def test_unsupported_source_fallback_has_actionable_error(artifact_root, tmp_path, monkeypatch):
    root, _, _ = artifact_root
    monkeypatch.setenv("NPDB_VALIDATOR_SOURCE_ONLY", "1")
    monkeypatch.setitem(BUILD["build_bundle"].__globals__, "source_directory",
                        lambda root: root / "tools/bids-validator-rust")
    with pytest.raises(RuntimeError, match="source fallback cannot.*matching native host"):
        BUILD["build_bundle"](root, tmp_path / "bundle", TARGET)


@pytest.mark.parametrize("data", [
    macho(cpu=0x01000007), macho(minimum=12 << 16),
    macho(library="/opt/homebrew/lib/libextra.dylib"), b"truncated",
])
def test_macho_rejects_wrong_architecture_baseline_or_libraries(data):
    with pytest.raises(RuntimeError):
        AUDIT["audit_macho"](data, 0x0100000C)


def test_universal_macho_is_accepted_for_both_architectures():
    intel = macho(cpu=0x01000007)
    arm = macho()
    header = struct.pack(">II", 0xCAFEBABE, 2)
    header += struct.pack(">IIIII", 0x01000007, 0, 48, len(intel), 0)
    header += struct.pack(">IIIII", 0x0100000C, 0, 48 + len(intel), len(arm), 0)
    for cpu in (0x01000007, 0x0100000C):
        AUDIT["audit_macho"](header + intel + arm, cpu)


def test_universal_macho_checks_the_other_slice_baseline_too():
    intel = macho(cpu=0x01000007, minimum=12 << 16)
    arm = macho()
    header = struct.pack(">II", 0xCAFEBABE, 2)
    header += struct.pack(">IIIII", 0x01000007, 0, 48, len(intel), 0)
    header += struct.pack(">IIIII", 0x0100000C, 0, 48 + len(intel), len(arm), 0)
    with pytest.raises(RuntimeError, match="baseline"):
        AUDIT["audit_macho"](header + intel + arm, 0x0100000C)


def test_approved_runtime_short_commit_does_not_weaken_source_verification(
    artifact_root, tmp_path,
):
    root, manifest, _ = artifact_root
    bundle = tmp_path / "runtime"
    bundle.mkdir()
    binary = bundle / "bids-validator"
    binary.write_bytes(macho())
    binary.chmod(0o755)
    schema = bundle / "schema.json"
    schema.write_text("{}")
    for name in ("LICENSE", "THIRD-PARTY-LICENSES"):
        (bundle / name).write_text("notice")
    provenance = {
        **source_identity(), "target": TARGET, "acquisition": "appveyor",
        "embedded_source_commit": source_identity()["source_commit"][:12],
        "archive_sha256": manifest["artifacts"][TARGET][0]["archive_sha256"],
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
    }
    pin = source_identity()
    version = (
        f"{pin['upstream_version']} commit {pin['upstream_commit'][:7]} "
        f"rust {pin['rust_package_version']}\nrust git commit {pin['source_commit'][:12]}\n"
    )
    module = root / "src/npdb/external/native.py"
    with (
        patch("npdb.external.native.__file__", str(module)),
        patch("npdb.external.native.subprocess.run",
              return_value=subprocess.CompletedProcess([], 0, stdout=version)),
    ):
        (bundle / "provenance.json").write_text(json.dumps(provenance))
        assert verify_bundle(bundle) == binary
        provenance["acquisition"] = "source"
        (bundle / "provenance.json").write_text(json.dumps(provenance))
        with pytest.raises(RuntimeError, match="version does not match"):
            verify_bundle(bundle)
