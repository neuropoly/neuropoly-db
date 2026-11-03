import hashlib
import io
import json
import runpy
import struct
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = runpy.run_path(str(ROOT / "tools/validator_windows.py"))
AUDIT = runpy.run_path(str(ROOT / "tools/audit_validator.py"))


def pe(*libraries, machine=0x8664):
    data = bytearray(4096)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, machine, 1)
    struct.pack_into("<H", data, 0x94, 240)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<I", data, 0x98 + 120, 0x1000 if libraries else 0)
    struct.pack_into("<IIII", data, 0x98 + 240 + 8, 2048, 0x1000, 2048, 512)
    name = 1024
    for index, library in enumerate(libraries):
        struct.pack_into("<I", data, 512 + index * 20 + 12, 0x1000 + name - 512)
        encoded = library.encode() + b"\0"
        data[name:name + len(encoded)] = encoded
        name += len(encoded)
    return data


@pytest.fixture
def runtime(tmp_path):
    directory = tmp_path / "authorized"
    directory.mkdir()
    (directory / "vcruntime140.dll").write_bytes(pe("vcruntime140_1.dll", "KERNEL32.dll"))
    (directory / "vcruntime140_1.dll").write_bytes(pe("vcruntime140.dll"))
    (directory / "terms.txt").write_text("fixture redistribution terms")
    manifest = {
        "authorized": True, "origin": "https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files",
        "version": "14.44", "license": "terms.txt",
        "license_sha256": hashlib.sha256((directory / "terms.txt").read_bytes()).hexdigest(),
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in directory.glob("*.dll")},
    }
    (directory / "redistribution.json").write_text(json.dumps(manifest))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    executable = bundle / "bids-validator.exe"
    executable.write_bytes(pe("vcruntime140.dll", "api-ms-win-crt-runtime-l1-1-0.dll"))
    return directory, executable


def test_authorized_transitive_runtime_closure_is_bundled(runtime):
    directory, executable = runtime
    WINDOWS["bundle"](executable, str(directory))
    AUDIT["audit"](executable, "win_amd64")
    assert (executable.parent / "vcruntime140.dll").is_file()
    assert (executable.parent / "vcruntime140_1.dll").is_file()
    assert (executable.parent / "WINDOWS-RUNTIME-LICENSE").is_file()
    assert set(json.loads((executable.parent / "windows-runtime.json").read_text())["files"]) == {
        "vcruntime140.dll", "vcruntime140_1.dll",
    }


@pytest.mark.parametrize("damage", ["checksum", "architecture", "terms", "authorization", "origin"])
def test_runtime_inputs_fail_closed(runtime, damage):
    directory, executable = runtime
    manifest_file = directory / "redistribution.json"
    manifest = json.loads(manifest_file.read_text())
    if damage == "checksum":
        (directory / "vcruntime140.dll").write_bytes(b"tampered")
    elif damage == "architecture":
        data = pe(machine=0x14C)
        (directory / "vcruntime140.dll").write_bytes(data)
        manifest["files"]["vcruntime140.dll"] = hashlib.sha256(data).hexdigest()
    elif damage == "terms":
        (directory / "terms.txt").unlink()
    elif damage == "authorization":
        manifest["authorized"] = False
    else:
        manifest["origin"] = "https://unofficial.invalid/runtime"
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):
        WINDOWS["bundle"](executable, str(directory))


def test_missing_transitive_runtime_is_rejected(runtime):
    directory, executable = runtime
    (directory / "vcruntime140_1.dll").unlink()
    with pytest.raises(RuntimeError, match="vcruntime140_1.dll"):
        WINDOWS["bundle"](executable, str(directory))


def test_audit_rejects_unbundled_runtime(runtime):
    _, executable = runtime
    with pytest.raises(RuntimeError, match="Unbundled Windows"):
        AUDIT["audit"](executable, "win_amd64")


@pytest.mark.parametrize("data", [b"", b"MZ", pe(machine=0x14C)])
def test_invalid_pe_is_an_explicit_failure(tmp_path, data):
    executable = tmp_path / "invalid.exe"
    executable.write_bytes(data)
    with pytest.raises(RuntimeError):
        AUDIT["pe_imports"](executable)


def test_delay_load_dependencies_are_included(tmp_path):
    data = pe("vcruntime140.dll")
    struct.pack_into("<I", data, 0x98 + 120, 0)
    struct.pack_into("<I", data, 0x98 + 216, 0x1000)
    data[512:576] = bytes(64)
    struct.pack_into("<II", data, 512, 1, 0x1200)
    executable = tmp_path / "delay.exe"
    executable.write_bytes(data)
    assert AUDIT["pe_imports"](executable) == {"vcruntime140.dll"}


def zipped(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


@pytest.fixture
def microsoft_downloads(monkeypatch):
    prefix = WINDOWS["RUNTIME_PREFIX"]
    package = {
        prefix + "vcruntime140.dll": pe("vcruntime140_1.dll"),
        prefix + "vcruntime140_1.dll": pe("KERNEL32.dll"),
        "Contents/VC/Redist/debug_nonredist/vcruntime140d.dll": b"not redistributable",
        "Contents/VC/Redist/onecore/x64/vcruntime140.dll": b"wrong variant",
    }
    terms = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body><w:p><w:r><w:t>MICROSOFT SOFTWARE LICENSE TERMS</w:t></w:r></w:p>
    <w:p><w:r><w:t>Fixture redistribution terms.</w:t></w:r></w:p></w:body></w:document>"""
    common = runpy.run_path(str(ROOT / "tools/validator_acquisition.py"))
    download = Mock(side_effect=[zipped(package), zipped({"word/document.xml": terms})])
    real_run_path = runpy.run_path

    def helpers(path, *args, **kwargs):
        if str(path).endswith("validator_acquisition.py"):
            return {"cached": download}
        return real_run_path(path, *args, **kwargs)

    monkeypatch.setattr(runpy, "run_path", helpers)
    return download, package, terms, common


def test_default_bundles_official_runtime_on_any_host(runtime, microsoft_downloads):
    _, executable = runtime
    downloads, _, _, _ = microsoft_downloads
    WINDOWS["bundle"](executable, "")
    AUDIT["audit"](executable, "win_amd64")
    manifest = json.loads((executable.parent / "windows-runtime.json").read_text())
    assert manifest["origin"] == WINDOWS["RUNTIME_URL"]
    assert manifest["archive_sha256"] == WINDOWS["RUNTIME_SHA256"]
    assert manifest["license_archive_sha256"] == WINDOWS["LICENSE_SHA256"]
    assert set(manifest["files"]) == {"vcruntime140.dll", "vcruntime140_1.dll"}
    assert (executable.parent / "WINDOWS-RUNTIME-LICENSE").read_text() == (
        "MICROSOFT SOFTWARE LICENSE TERMS\nFixture redistribution terms.\n"
    )
    assert [call.args[1:] for call in downloads.call_args_list] == [
        (WINDOWS["RUNTIME_URL"], WINDOWS["RUNTIME_SHA256"], "windows-runtime"),
        (WINDOWS["LICENSE_URL"], WINDOWS["LICENSE_SHA256"], "windows-runtime"),
    ]


def test_explicit_runtime_bypasses_download(runtime, microsoft_downloads):
    directory, executable = runtime
    downloads, _, _, _ = microsoft_downloads
    WINDOWS["bundle"](executable, str(directory))
    downloads.assert_not_called()


def test_static_binary_needs_no_runtime_download(tmp_path, microsoft_downloads):
    downloads, _, _, _ = microsoft_downloads
    executable = tmp_path / "static.exe"
    executable.write_bytes(pe("KERNEL32.dll"))
    WINDOWS["bundle"](executable, "")
    downloads.assert_not_called()


@pytest.mark.parametrize("damage", ["traversal", "corrupt", "missing-dll", "bad-xml", "no-terms"])
def test_invalid_downloads_fail_explicitly(runtime, microsoft_downloads, damage):
    _, executable = runtime
    downloads, package, terms, _ = microsoft_downloads
    if damage == "traversal":
        package[WINDOWS["RUNTIME_PREFIX"] + "../escape.dll"] = pe()
    elif damage == "missing-dll":
        package = {"debug_nonredist/vcruntime140d.dll": pe()}
    elif damage == "bad-xml":
        terms = b"bad XML"
    elif damage == "no-terms":
        terms = b"<document/>"
    downloads.side_effect = [
        b"not a zip" if damage == "corrupt" else zipped(package),
        zipped({"word/document.xml": terms}),
    ]
    with pytest.raises(RuntimeError, match="Cannot prepare the Windows runtime"):
        WINDOWS["bundle"](executable, "")
    assert not (executable.parent / "vcruntime140.dll").exists()


def test_download_failure_has_actionable_override(runtime, microsoft_downloads):
    _, executable = runtime
    downloads, _, _, _ = microsoft_downloads
    downloads.side_effect = OSError("network unavailable")
    with pytest.raises(RuntimeError, match="network unavailable.*") as error:
        WINDOWS["bundle"](executable, "")
    assert "--windows-runtime-dir" in str(error.value)


def test_cached_runtime_is_verified_and_works_offline(runtime, microsoft_downloads, monkeypatch, tmp_path):
    _, executable = runtime
    downloads, package, terms, common = microsoft_downloads
    namespace = tmp_path / "cache/windows-runtime"
    namespace.mkdir(parents=True)
    runtime_bytes = zipped(package)
    terms_bytes = zipped({"word/document.xml": terms})
    for data in (runtime_bytes, terms_bytes):
        (namespace / hashlib.sha256(data).hexdigest()).write_bytes(data)
    monkeypatch.setenv("NPDB_VALIDATOR_CACHE", str(namespace.parent))
    globals_ = WINDOWS["bundle"].__globals__
    monkeypatch.setitem(globals_, "RUNTIME_SHA256", hashlib.sha256(runtime_bytes).hexdigest())
    monkeypatch.setitem(globals_, "LICENSE_SHA256", hashlib.sha256(terms_bytes).hexdigest())
    downloads.side_effect = common["cached"]
    monkeypatch.setitem(common["cached"].__globals__, "download",
                        Mock(side_effect=AssertionError("Must not use network")))
    WINDOWS["bundle"](executable, "")
    AUDIT["audit"](executable, "win_amd64")
    (namespace / hashlib.sha256(runtime_bytes).hexdigest()).write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        WINDOWS["bundle"](executable, "")


def test_relative_runtime_override_survives_staged_build_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    common = runpy.run_path(str(ROOT / "tools/validator_acquisition.py"))
    settings = common["configuration"]("github", runtime="authorized")
    assert settings["runtime"] == str(tmp_path / "authorized")
    assert common["environment"](settings)["NPDB_WINDOWS_RUNTIME_DIR"] == str(tmp_path / "authorized")
