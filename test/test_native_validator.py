import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from npdb.external.native import source_identity, validator_executable, verify_bundle

ROOT = Path(__file__).resolve().parents[1]
BUILD = runpy.run_path(str(ROOT / "tools/build_validator.py"))
AUDIT = runpy.run_path(str(ROOT / "tools/audit_validator.py"))


def _bundle(directory):
    directory.mkdir(parents=True)
    pin = source_identity()
    executable = directory / "bids-validator"
    executable.write_text(
        "#!/bin/sh\nprintf '%s\\n' "
        f"'{pin['upstream_version']} commit {pin['upstream_commit'][:7]} "
        f"rust {pin['rust_package_version']}' "
        f"'rust git commit {pin['source_commit']}'\n"
    )
    executable.chmod(0o755)
    schema = directory / "schema.json"
    schema.write_text("{}")
    for notice in ("LICENSE", "THIRD-PARTY-LICENSES"):
        (directory / notice).write_text("fixture notice")
    (directory / "provenance.json").write_text(json.dumps({
        **pin, "target": "x86_64-unknown-linux-gnu",
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
    }))
    return executable


def test_installer_reuses_a_verified_bundle_without_cargo(tmp_path):
    prefix = tmp_path / "environment"
    executable = _bundle(prefix / "bin/npdb-validator")
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install_bids_validator.sh"), str(prefix)],
        env={**os.environ, "PATH": "/usr/bin:/bin", "PYTHON": str(ROOT / ".venv/bin/python")},
        check=True, capture_output=True, text=True,
    )
    assert "already installed" in result.stdout
    assert (prefix / "bin/bids-validator").read_bytes() == executable.read_bytes()


def test_source_development_finds_a_verified_bundle_beside_python(tmp_path):
    executable = _bundle(tmp_path / "npdb-validator")
    (tmp_path / "bids-validator").write_bytes(executable.read_bytes())
    with (
        patch("npdb.external.native.sys.executable", str(tmp_path / "python")),
        patch("npdb.external.native.shutil.which", return_value=None),
    ):
        assert validator_executable() == str(executable)


@pytest.mark.parametrize("damage", ["binary", "schema", "identity", "notice", "json"])
def test_damaged_bundles_fail_explicitly(tmp_path, damage):
    executable = _bundle(tmp_path / "bundle")
    if damage == "binary":
        executable.write_text("changed")
    elif damage == "schema":
        (executable.parent / "schema.json").write_text("changed")
    elif damage == "notice":
        (executable.parent / "LICENSE").unlink()
    elif damage == "json":
        (executable.parent / "provenance.json").write_text("[]")
    else:
        provenance = executable.parent / "provenance.json"
        data = json.loads(provenance.read_text())
        data["source_commit"] = "wrong"
        provenance.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="Invalid native validator bundle"):
        verify_bundle(executable.parent)


@pytest.mark.parametrize("data", ["[]", "{}", "{", '{"repository": false}'])
def test_damaged_package_identity_is_an_explicit_error(data):
    with (
        patch("npdb.external.native.Path.read_text", return_value=data),
        pytest.raises(RuntimeError, match="Cannot read.*source identity"),
    ):
        source_identity()


def test_missing_packaged_bundle_never_uses_path(tmp_path):
    module = tmp_path / "site-packages/npdb/external/native.py"
    module.parent.mkdir(parents=True)
    with (
        patch("npdb.external.native.__file__", str(module)),
        patch("npdb.external.native.shutil.which") as which,
        pytest.raises(RuntimeError, match="bundle is missing"),
    ):
        validator_executable()
    which.assert_not_called()


def test_error_diagnostics_use_the_bundled_validator_not_path():
    from npdb.cli.errors import _environment

    with patch("npdb.external.native.validator_executable", return_value="/package/_bin/bids-validator"):
        assert dict(_environment())["Native validator"] == "/package/_bin/bids-validator"
    with patch("npdb.external.native.validator_executable", side_effect=RuntimeError("damaged bundle")):
        assert dict(_environment())["Native validator"] == "damaged bundle"


def test_build_rejects_an_uninitialized_submodule_without_fetching(tmp_path):
    with (
        patch("subprocess.run") as run,
        pytest.raises(RuntimeError, match="Pinned validator source is missing"),
    ):
        BUILD["source_directory"](tmp_path)
    run.assert_not_called()


@pytest.fixture
def exported_validator_source(tmp_path):
    source = tmp_path / "tools/bids-validator-rust"
    source.mkdir(parents=True)
    for name in ("Cargo.toml", "Cargo.lock", "build.rs", "LICENSE"):
        (source / name).write_text("fixture\n")
    (source / "schemas").mkdir()
    (source / "schemas/schema-1.2.1.json").write_text("{}")
    external = tmp_path / "src/npdb/external"
    external.mkdir(parents=True)
    shutil.copy2(ROOT / "src/npdb/external/native.py", external / "native.py")
    (external / "validator-source.json").write_text(json.dumps({
        **source_identity(),
        "source_tree_sha256": hashlib.sha256(json.dumps(
            BUILD["source_manifest"](tmp_path), sort_keys=True,
        ).encode()).hexdigest(),
    }))
    shutil.copy2(ROOT / "tools/build_validator.py", tmp_path / "tools/build_validator.py")
    return tmp_path


def test_exported_source_is_verified_without_git_metadata(exported_validator_source):
    root = exported_validator_source
    with patch("subprocess.run") as run:
        assert BUILD["source_directory"](root) == root / "tools/bids-validator-rust"
    run.assert_not_called()
    (root / "tools/bids-validator-rust/Cargo.toml").write_text("tampered\n")
    with pytest.raises(RuntimeError, match="pinned source digest"):
        BUILD["source_directory"](root)


@pytest.mark.parametrize("stage", ["rev-parse", "status", "-vV", "build", "metadata"])
def test_build_cli_reports_subprocess_diagnostics(exported_validator_source, capsys, stage):
    root = exported_validator_source
    if stage in {"rev-parse", "status"}:
        (root / "tools/bids-validator-rust/.git").write_text("gitdir: /missing/metadata\n")

    def run(command, **kwargs):
        if stage in command:
            raise subprocess.CalledProcessError(
                128, command, output="captured standard output\n",
                stderr="fatal: underlying failure\n",
            )
        output = ""
        if "rev-parse" in command:
            output = source_identity()["source_commit"]
        elif "-vV" in command:
            output = "host: x86_64-unknown-linux-gnu\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    with (
        patch.object(sys, "argv", ["build_validator.py", "--output", str(root / "bundle")]),
        patch("shutil.which", return_value="/fixture/cargo"),
        patch("subprocess.run", side_effect=run),
        pytest.raises(SystemExit) as error,
    ):
        runpy.run_path(str(root / "tools/build_validator.py"), run_name="__main__")
    assert error.value.code == 1
    diagnostics = capsys.readouterr().err
    assert stage in diagnostics
    assert "exit status 128" in diagnostics
    assert "captured standard output" in diagnostics
    assert "fatal: underlying failure" in diagnostics


def test_build_forces_cargo_outputs_outside_the_submodule(tmp_path):
    source = ROOT / "tools/bids-validator-rust"
    original = BUILD["build_bundle"].__globals__["source_directory"]
    try:
        BUILD["build_bundle"].__globals__["source_directory"] = lambda root: source
        with (
            patch.dict(os.environ, {"CARGO_TARGET_DIR": str(source / "target")}),
            patch("shutil.which", return_value="/cargo"),
            patch("subprocess.run", side_effect=RuntimeError("stop before compilation")) as run,
            pytest.raises(RuntimeError, match="stop before compilation"),
        ):
            BUILD["build_bundle"](ROOT, tmp_path / "bundle", "x86_64-unknown-linux-gnu")
        command = run.call_args.args[0]
        assert Path(command[command.index("--target-dir") + 1]).is_relative_to(ROOT / "build")
        assert not Path(run.call_args.kwargs["env"]["CARGO_TARGET_DIR"]).is_relative_to(source)
        assert "--locked" in command
    finally:
        BUILD["build_bundle"].__globals__["source_directory"] = original


def test_build_rejects_writes_inside_the_submodule():
    with pytest.raises(RuntimeError, match="outside the submodule"):
        BUILD["build_bundle"](ROOT, ROOT / "tools/bids-validator-rust/target")


def test_installer_rejects_a_prefix_inside_the_submodule():
    with pytest.raises(RuntimeError, match="outside the validator submodule"):
        BUILD["install"](ROOT, ROOT / "tools/bids-validator-rust")


def test_source_fingerprint_is_platform_independent(tmp_path):
    source = tmp_path / "tools/bids-validator-rust"
    source.mkdir(parents=True)
    for name in ("Cargo.toml", "Cargo.lock", "build.rs", "LICENSE"):
        (source / name).write_bytes(b"first\nsecond\n")
    unix = BUILD["source_manifest"](tmp_path)
    for path in source.iterdir():
        path.write_bytes(b"first\r\nsecond\r\n")
    assert BUILD["source_manifest"](tmp_path) == unix
    assert all("\\" not in name for name in unix)


def test_native_build_preserves_local_submodule_checkout_and_update(tmp_path):
    def git(directory, *args):
        return subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "-C", str(directory), *args],
            check=True, capture_output=True, text=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
                 "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                 "GIT_COMMITTER_EMAIL": "fixture@example.invalid"},
        ).stdout.strip()

    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init")
    (upstream / "src").mkdir()
    (upstream / "schemas").mkdir()
    for name in ("Cargo.toml", "Cargo.lock", "build.rs", "LICENSE"):
        (upstream / name).write_text("fixture\n")
    (upstream / "schemas/schema-1.2.1.json").write_text("{}")
    (upstream / "src/main.rs").write_text("first\n")
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "First fixture source")
    first_source = git(upstream, "rev-parse", "HEAD")
    (upstream / "src/main.rs").write_text("second\n")
    git(upstream, "commit", "-am", "Second fixture source")
    second_source = git(upstream, "rev-parse", "HEAD")

    parent = tmp_path / "parent"
    parent.mkdir()
    git(parent, "init")
    git(parent, "submodule", "add", str(upstream), "tools/bids-validator-rust")
    source = parent / "tools/bids-validator-rust"
    external = parent / "src/npdb/external"
    external.mkdir(parents=True)
    shutil.copy2(ROOT / "src/npdb/external/native.py", external / "native.py")

    def record(revision):
        git(source, "checkout", revision)
        (external / "validator-source.json").write_text(json.dumps({
            **source_identity(), "source_commit": revision,
            "source_tree_sha256": hashlib.sha256(json.dumps(
                BUILD["source_manifest"](parent), sort_keys=True,
            ).encode()).hexdigest(),
        }))
        git(parent, "add", ".")
        git(parent, "commit", "-m", "Pin fixture source")
        return git(parent, "rev-parse", "HEAD")

    first_parent = record(first_source)
    second_parent = record(second_source)
    cargo = tmp_path / "cargo"
    cargo.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'build':\n"
        "    output = pathlib.Path(args[args.index('--target-dir') + 1])\n"
        "    output /= args[args.index('--target') + 1] + '/release'\n"
        "    output.mkdir(parents=True)\n"
        "    (output / 'bids-validator').write_text('fixture executable')\n"
        "elif args[0] == 'metadata':\n"
        "    print(json.dumps({'packages': []}))\n"
    )
    cargo.chmod(0o755)
    with patch("shutil.which", return_value=str(cargo)):
        BUILD["build_bundle"](parent, parent / "build/bundle", "x86_64-unknown-linux-gnu")
    assert not (source / "target").exists()
    assert git(source, "status", "--porcelain") == ""
    for revision, expected_source in (
        (first_parent, first_source), (second_parent, second_source),
    ):
        git(parent, "checkout", "--recurse-submodules", revision)
        git(parent, "submodule", "update", "--init", "--checkout", "--no-fetch",
            "--", "tools/bids-validator-rust")
        assert git(source, "rev-parse", "HEAD") == expected_source
        assert git(source, "status", "--porcelain") == ""


@pytest.mark.parametrize("platform,library,versions,passes", [
    ("manylinux_2_28_x86_64", "libpthread.so.0", "GLIBC_2.28", True),
    ("manylinux_2_28_x86_64", "libdl.so.2", "GLIBC_2.28", True),
    ("manylinux_2_28_x86_64", "libc.so.6", "GLIBC_2.29", False),
    ("manylinux_2_28_x86_64", "libssl.so.3", "GLIBC_2.28", False),
    ("musllinux_1_2_x86_64", "libc.so.6", "GLIBC_2.28", False),
])
def test_native_audit_enforces_runtime_baselines(platform, library, versions, passes):
    with patch("subprocess.run", side_effect=[
        Mock(stdout="Advanced Micro Devices X86-64"),
        Mock(stdout=f"Shared library: [{library}]"),
        Mock(stdout=versions),
    ]):
        if passes:
            AUDIT["audit"](Path("validator"), platform)
        else:
            with pytest.raises(RuntimeError):
                AUDIT["audit"](Path("validator"), platform)
