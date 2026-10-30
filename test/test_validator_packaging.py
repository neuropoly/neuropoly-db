import json
import os
import runpy
import struct
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from bids_repair.native import NativeValidatorError
from npdb.cli.errors import _diagnose_one

ROOT = Path(__file__).resolve().parents[1]
build_bundle = runpy.run_path(str(ROOT / "tools/build_validator.py"))["build_bundle"]
source_directory = runpy.run_path(str(ROOT / "tools/build_validator.py"))[
    "source_directory"
]
audit = runpy.run_path(str(ROOT / "tools/audit_validator.py"))["audit"]


def test_missing_validator_has_actionable_diagnostics():
    diagnosis = _diagnose_one(NativeValidatorError("The validator bundle is missing."))

    assert diagnosis is not None
    assert diagnosis.title == "Native BIDS validator unavailable"
    assert diagnosis.hint is not None
    assert "Reinstall a matching npdb wheel" in diagnosis.hint


def test_source_pin_and_schema_are_consistent():
    pin = json.loads((ROOT / "tools/bids-validator-source.json").read_text())
    manifest = tomllib.loads(
        (ROOT / "tools/bids-validator-rust/Cargo.toml").read_text()
    )
    assert manifest["package"]["version"] == pin["rust_package_version"]
    assert manifest["package"]["license"] == "MIT"
    assert pin["schema_license"] == "CC-BY-4.0"
    assert (ROOT / "tools/bids-schema-NOTICE").is_file()
    assert manifest["bin"][0]["name"] == "bids-validator"
    assert (ROOT / "tools/bids-validator-rust/crates/bids-core/src/lib.rs").is_file()
    assert (ROOT / "tools/bids-validator-rust/schemas/schema-1.2.1.json").is_file()


def test_missing_cargo_cannot_produce_a_successful_bundle(tmp_path):
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="prebuilt npdb wheel"),
    ):
        build_bundle(ROOT, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_uninitialized_submodule_has_actionable_error(tmp_path):
    with pytest.raises(RuntimeError, match="git submodule update --init"):
        source_directory(tmp_path)

    with (
        patch("subprocess.run") as run,
        pytest.raises(RuntimeError, match="not a GitHub source archive"),
    ):
        build_bundle(tmp_path, tmp_path / "bundle")

    run.assert_not_called()
    assert not (tmp_path / "bundle").exists()


def test_build_locks_dependencies_and_preserves_source_identity_and_notices(tmp_path):
    target = "x86_64-unknown-linux-gnu"
    binary = tmp_path / "target" / target / "release" / "bids-validator"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture executable")
    destination = tmp_path / "bundle"
    metadata = {"target_directory": str(tmp_path / "target"), "packages": []}

    with (
        patch("shutil.which", return_value="/fake/cargo"),
        patch(
            "subprocess.run",
            side_effect=[Mock(), Mock(stdout=json.dumps(metadata))],
        ) as run,
    ):
        result = build_bundle(ROOT, destination, target)

    build = run.call_args_list[0]
    assert "--locked" in build.args[0]
    assert "--release" in build.args[0]
    pin = json.loads((ROOT / "tools/bids-validator-source.json").read_text())
    assert build.kwargs["env"]["BIDS_VALIDATOR_GIT_COMMIT"] == pin["source_commit"]
    assert result.read_bytes() == binary.read_bytes()
    assert "BIDS Contributors" in (destination / "THIRD-PARTY-LICENSES").read_text()
    assert json.loads((destination / "provenance.json").read_text())["target"] == target


@pytest.mark.parametrize(
    "architecture,machine,loader",
    [
        ("x86_64", "Advanced Micro Devices X86-64", "ld-linux-x86-64.so.2"),
        ("aarch64", "AArch64", "ld-linux-aarch64.so.1"),
    ],
)
def test_accepts_manylinux_glibc_runtime_libraries(
    tmp_path, architecture, machine, loader
):
    libraries = [
        "libc.so.6",
        "libdl.so.2",
        "libgcc_s.so.1",
        "libm.so.6",
        "libpthread.so.0",
        loader,
    ]
    outputs = [
        Mock(stdout=f"Machine: {machine}"),
        Mock(stdout="\n".join(f"Shared library: [{name}]" for name in libraries)),
        Mock(stdout="GLIBC_2.17\nGLIBC_2.28"),
    ]

    with patch("subprocess.run", side_effect=outputs):
        audit(tmp_path / "binary", f"manylinux_2_28_{architecture}")


@pytest.mark.parametrize(
    "architecture,machine",
    [("x86_64", "Advanced Micro Devices X86-64"), ("aarch64", "AArch64")],
)
def test_rejects_linux_binary_above_claimed_glibc_baseline(
    tmp_path, architecture, machine
):
    outputs = [
        Mock(stdout=f"Machine: {machine}"),
        Mock(stdout="Shared library: [libpthread.so.0]\nShared library: [libdl.so.2]"),
        Mock(stdout="GLIBC_2.34"),
    ]
    with (
        patch("subprocess.run", side_effect=outputs),
        pytest.raises(RuntimeError, match="above 2.28"),
    ):
        audit(tmp_path / "binary", f"manylinux_2_28_{architecture}")


def test_rejects_unbundled_linux_runtime_dependency(tmp_path):
    outputs = [
        Mock(stdout="Machine: Advanced Micro Devices X86-64"),
        Mock(stdout="Shared library: [libcustom.so]"),
        Mock(stdout="GLIBC_2.28"),
    ]
    with (
        patch("subprocess.run", side_effect=outputs),
        pytest.raises(RuntimeError, match="Unbundled native dependencies"),
    ):
        audit(tmp_path / "binary", "manylinux_2_28_x86_64")


@pytest.mark.parametrize("library", ["libpthread.so.0", "libdl.so.2"])
def test_rejects_glibc_runtime_libraries_in_musllinux(tmp_path, library):
    outputs = [
        Mock(stdout="Machine: AArch64"),
        Mock(stdout=f"Shared library: [{library}]"),
        Mock(stdout=""),
    ]

    with (
        patch("subprocess.run", side_effect=outputs),
        pytest.raises(RuntimeError, match="Unbundled native dependencies"),
    ):
        audit(tmp_path / "binary", "musllinux_1_2_aarch64")


def test_rejects_wrong_linux_architecture(tmp_path):
    with (
        patch("subprocess.run", return_value=Mock(stdout="Machine: AArch64")),
        pytest.raises(RuntimeError, match="architecture"),
    ):
        audit(tmp_path / "binary", "manylinux_2_28_x86_64")


def test_accepts_matching_macos_architecture_and_baseline(tmp_path):
    outputs = [
        Mock(stdout="arm64\n"),
        Mock(stdout="minos 11.0.0\n"),
        Mock(stdout="binary:\n\t/usr/lib/libSystem.B.dylib\n"),
    ]
    with patch("subprocess.run", side_effect=outputs):
        audit(tmp_path / "binary", "macosx_11_0_arm64")


def test_rejects_wrong_macos_architecture(tmp_path):
    with (
        patch("subprocess.run", return_value=Mock(stdout="arm64\n")),
        pytest.raises(RuntimeError, match="architecture"),
    ):
        audit(tmp_path / "binary", "macosx_11_0_x86_64")


def test_rejects_macos_binary_above_claimed_baseline(tmp_path):
    with (
        patch(
            "subprocess.run",
            side_effect=[Mock(stdout="x86_64\n"), Mock(stdout="minos 12.0\n")],
        ),
        pytest.raises(RuntimeError, match="baseline"),
    ):
        audit(tmp_path / "binary", "macosx_11_0_x86_64")


@pytest.mark.parametrize(
    "dll,accepted",
    [
        ("kernel32.dll", True),
        ("bcryptprimitives.dll", True),
        ("vcruntime140.dll", False),
    ],
)
def test_windows_runtime_dependency_policy(tmp_path, dll, accepted):
    data = bytearray(0x400)
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 1)
    struct.pack_into("<H", data, 0x94, 0xF0)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<I", data, 0x98 + 120, 0x1000)
    struct.pack_into("<IIII", data, 0x188 + 8, 0x200, 0x1000, 0x200, 0x200)
    struct.pack_into("<I", data, 0x200 + 12, 0x1040)
    encoded = dll.encode("ascii") + b"\0"
    data[0x240 : 0x240 + len(encoded)] = encoded
    binary = tmp_path / "binary.exe"
    binary.write_bytes(data)

    if accepted:
        audit(binary, "win_amd64")
    else:
        with pytest.raises(RuntimeError, match="runtime dependency"):
            audit(binary, "win_amd64")


@pytest.mark.skipif(os.name == "nt", reason="Unix development installer")
def test_development_installer_reuses_matching_binary_without_cargo(tmp_path):
    source = tmp_path / "tools"
    source.mkdir()
    binary = source / "bids-validator"
    binary.write_text(
        "#!/bin/sh\nprintf '%s\\n' "
        "'bids-validator 2.4.1 commit 94ea971 rust 0.1.20260517' "
        "'rust git commit b5c11828e104be88034ecccbe220ad1a20c72dfe'\n"
    )
    binary.chmod(0o755)
    prefix = tmp_path / "venv"
    subprocess.run(
        ["bash", str(ROOT / "scripts/install_bids_validator.sh"), str(prefix)],
        env={**os.environ, "PATH": f"{source}:/usr/bin:/bin"},
        check=True,
        capture_output=True,
        text=True,
    )
    assert (prefix / "bin/bids-validator").read_bytes() == binary.read_bytes()
